"""Cluster review progress keeps per-repository state without changing Gate rules."""

from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path

from dep_intel.cluster_review import (
    _is_origin,
    aggregate_counters,
    detect_repository_change,
    execute_cluster_review,
    load_cluster_review,
    new_cluster_review,
    preview_cluster_review,
    public_cluster_review,
    request_cluster_cancel,
)
from dep_intel.config import Settings
from dep_intel.graph_models import Snapshot
from dep_intel.review_engine import ReviewEngine
from dep_intel.review_progress import REVIEW_STAGES, progress_event


def test_cluster_review_id_tracks_no_change_and_one_review(tmp_path: Path) -> None:
    events: list[tuple[str, dict]] = []
    record = _record(tmp_path)
    calls: list[str] = []

    def review(member: dict, body: dict) -> dict:
        calls.append(member["service_id"])
        assert body["review_scope"] == "service_cluster"
        assert body["cluster_context"]["cluster_review_id"] == record["cluster_review_id"]
        assert body["cluster_context"]["repository"] == "repo-b"
        stored = load_cluster_review(tmp_path / "output", record["cluster_review_id"])
        assert stored is not None
        assert stored["repositories"][0]["status"] == "no_change"
        assert stored["repositories"][1]["status"] == "reviewing"
        return _report("review-b", "warn", [member["service_id"]])

    result = execute_cluster_review(
        record,
        settings=_settings(tmp_path),
        body=_body(),
        publish=lambda name, data: events.append((name, data)),
        detect=_detect({"repo-b": 4}),
        review=review,
    )
    progress = [data for name, data in events if name == "cluster_review_progress"]
    assert progress
    assert {item["cluster_review_id"] for item in progress} == {record["cluster_review_id"]}
    assert calls == ["repo-b"]
    statuses = [(item.get("service_id"), item.get("repository_status")) for item in progress if item.get("repository_status")]
    assert ("repo-a", "inspecting") in statuses
    assert ("repo-a", "no_change") in statuses
    assert ("repo-b", "inspecting") in statuses
    assert ("repo-b", "reviewing") in statuses
    assert ("repo-b", "completed") in statuses
    assert ("repo-c", "inspecting") in statuses
    assert ("repo-c", "no_change") in statuses
    for payload in progress:
        _assert_counters(payload)
        assert "findings" not in payload
        assert "ranked_candidates" not in payload
        assert "diff_text" not in payload
        assert "repository_path" not in payload
    completed = next(item for item in progress if item.get("service_id") == "repo-b" and item.get("repository_status") == "completed")
    assert completed["decision"] == "warn"
    assert completed["validation_status"] == "validated"
    assert completed["changed_file_count"] == 4
    assert completed["review_id"] == "review-b"
    no_change = next(item for item in progress if item.get("service_id") == "repo-a" and item.get("repository_status") == "no_change")
    assert no_change["decision"] == ""
    assert no_change["change_detected"] is False
    assert result["status"] == "completed"
    assert result["assessment"]["label"] == "ATTENTION REQUIRED"
    assert result["assessment"]["warnings"] == 1
    assert result["assessment"]["passes"] == 0
    assert result["assessment"]["repositories_no_change"] == 2
    restored = public_cluster_review(load_cluster_review(tmp_path / "output", record["cluster_review_id"]))
    assert restored["sequence"] == result["sequence"]
    assert [item["status"] for item in restored["repositories"]] == ["no_change", "completed", "no_change"]
    assert restored["repositories"][1]["review_id"] == "review-b"


def test_multiple_changed_repositories_keep_independent_reviews(tmp_path: Path) -> None:
    record = _record(tmp_path)
    seen: list[str] = []

    def review(member: dict, _body: dict) -> dict:
        seen.append(member["service_id"])
        decision = "pass" if member["service_id"] == "repo-a" else "block"
        return _report(f"review-{member['service_id']}", decision, [member["service_id"]])

    result = execute_cluster_review(
        record,
        settings=_settings(tmp_path),
        body=_body(),
        publish=lambda _name, _data: None,
        detect=_detect({"repo-a": 2, "repo-b": 3}),
        review=review,
    )
    assert seen == ["repo-a", "repo-b"]
    rows = {item["service_id"]: item for item in result["repositories"]}
    assert rows["repo-a"]["review_id"] == "review-repo-a"
    assert rows["repo-b"]["review_id"] == "review-repo-b"
    assert rows["repo-a"]["review_id"] != rows["repo-b"]["review_id"]
    assert rows["repo-a"]["decision"] == "pass"
    assert rows["repo-a"]["validation_status"] == "validated"
    assert rows["repo-b"]["decision"] == "block"
    assert rows["repo-c"]["status"] == "no_change"
    assert rows["repo-c"]["review_id"] == ""
    assert result["cluster_review_id"] == record["cluster_review_id"]
    assert result["assessment"]["verified_blockers"] == 1
    assert result["assessment"]["passes"] == 1
    assert result["assessment"]["evidenced_service_count"] == 2
    assert result["counters"]["reviews_completed"] == 2
    assert result["counters"]["repositories_no_change"] == 1


def test_repository_failure_is_kept_and_remaining_repositories_continue(tmp_path: Path) -> None:
    record = _record(tmp_path)

    def review(member: dict, _body: dict) -> dict:
        if member["service_id"] == "repo-b":
            raise RuntimeError("graph unavailable")
        return _report("review-a", "pass", ["repo-a"])

    result = execute_cluster_review(
        record,
        settings=_settings(tmp_path),
        body=_body(),
        publish=lambda _name, _data: None,
        detect=_detect({"repo-a": 1, "repo-b": 2}),
        review=review,
    )
    rows = {item["service_id"]: item for item in result["repositories"]}
    assert rows["repo-a"]["status"] == "completed"
    assert rows["repo-a"]["decision"] == "pass"
    assert rows["repo-b"]["status"] == "failed"
    assert rows["repo-b"]["error_code"] == "review_failed"
    assert "graph unavailable" in rows["repo-b"]["error_summary"]
    assert rows["repo-c"]["status"] == "no_change"
    assert result["status"] == "incomplete"
    assert result["assessment"]["complete"] is False
    assert result["counters"]["reviews_failed"] == 1
    assert result["counters"]["reviews_completed"] == 1


def test_cancellation_keeps_finished_repositories_and_stops_the_rest(tmp_path: Path) -> None:
    record = _record(tmp_path)
    cancel = threading.Event()

    def review(member: dict, _body: dict) -> dict:
        cancel.set()
        return _report(f"review-{member['service_id']}", "warn", [member["service_id"]])

    result = execute_cluster_review(
        record,
        settings=_settings(tmp_path),
        body=_body(),
        publish=lambda _name, _data: None,
        cancel=cancel,
        detect=_detect({"repo-a": 1, "repo-b": 1}),
        review=review,
    )
    rows = {item["service_id"]: item for item in result["repositories"]}
    assert rows["repo-a"]["status"] == "completed"
    assert rows["repo-a"]["decision"] == "warn"
    assert rows["repo-b"]["status"] == "cancelled"
    assert rows["repo-c"]["status"] == "cancelled"
    assert result["status"] == "cancelled"
    assert result["assessment"]["label"] == "CANCELLED"
    assert result["assessment"]["complete"] is False
    stored = load_cluster_review(tmp_path / "output", record["cluster_review_id"])
    assert stored is not None
    assert stored["status"] == "cancelled"


def test_cancel_before_start_does_not_review_repositories(tmp_path: Path) -> None:
    record = _record(tmp_path)
    called = False

    def review(_member: dict, _body: dict) -> dict:
        nonlocal called
        called = True
        return _report("should-not-run", "pass", [])

    cancel = threading.Event()
    cancel.set()
    result = execute_cluster_review(
        record,
        settings=_settings(tmp_path),
        body=_body(),
        publish=lambda _name, _data: None,
        cancel=cancel,
        detect=_detect({"repo-a": 1}),
        review=review,
    )
    assert called is False
    assert result["status"] == "cancelled"
    assert [item["status"] for item in result["repositories"]] == ["cancelled", "cancelled", "cancelled"]


def test_request_cancel_is_visible_before_the_run_finishes(tmp_path: Path) -> None:
    record = _record(tmp_path)
    from dep_intel.cluster_review import save_cluster_review

    save_cluster_review(tmp_path / "output", record)
    payload = request_cluster_cancel(tmp_path / "output", record["cluster_review_id"])
    assert payload is not None
    assert payload["cancel_requested"] is True
    assert payload["status"] == "running"


def test_working_tree_preview_counts_changes_without_a_gate(tmp_path: Path) -> None:
    root = tmp_path
    repos = [_git_repo(root, name) for name in ("repo-a", "repo-b", "repo-c")]
    (repos[1] / "README.md").write_text("changed\n", encoding="utf-8")
    settings = _settings(root)
    settings.repos_file.write_text(
        "[cluster:demo]\nrepo-a = repo-a\nrepo-b = repo-b\nrepo-c = repo-c\n",
        encoding="utf-8",
    )
    snapshot = Snapshot(
        version="test",
        generated_at="",
        partitions={},
        nodes={},
        edges=[],
        forward={},
        reverse={},
        by_service={},
        by_file={},
        by_name={},
        contracts={},
        revision_vector={},
        declared_clusters=[{"id": "demo", "services": ["repo-a", "repo-b", "repo-c"]}],
    )
    preview = preview_cluster_review(
        settings,
        {"review_scope": "service_cluster", "cluster_id": "demo", "source": {"kind": "working_tree"}},
        snapshot,
    )
    assert preview["repository_total"] == 3
    assert preview["changed_count"] == 1
    assert preview["no_change_count"] == 2
    changed = [item for item in preview["repositories"] if item["change_detected"]]
    assert changed[0]["service_id"] == "repo-b"
    assert changed[0]["changed_file_count"] == 1
    assert "decision" not in changed[0]
    detected = detect_repository_change(repos[0], {"kind": "working_tree"}, settings)
    assert detected["change_detected"] is False
    detected = detect_repository_change(repos[1], {"kind": "working_tree"}, settings)
    assert detected["changed_file_count"] == 1


def test_repository_review_progress_gains_cluster_context_without_changing_stages(tmp_path: Path) -> None:
    events: list[tuple[str, dict]] = []
    repo = _git_repo(tmp_path, "repo")
    (repo / "src").mkdir()
    (repo / "src" / "app.js").write_text("function ok() { return 1 }\n", encoding="utf-8")
    _commit(repo, "base")
    (repo / "src" / "app.js").write_text("function ok() { return 2 }\n", encoding="utf-8")
    context = {
        "cluster_review_id": "cluster-1",
        "cluster_id": "demo",
        "cluster_label": "Demo",
        "repository": "repo",
        "service_id": "repo",
        "repository_index": 2,
        "repository_total": 3,
    }
    engine = ReviewEngine(_settings(tmp_path), cluster=None, publish=lambda name, data: events.append((name, data)))
    report = engine.run({
        "source": {"kind": "working_tree", "repository": "repo"},
        "verify": False,
        "job_id": "job-1",
        "config": {"policies": [], "verification": {"enabled": False, "commands": []}},
        "cluster_context": context,
    })
    progress = [data for name, data in events if name == "review_progress"]
    stages = {stage for stage, _label in REVIEW_STAGES}
    assert [item["stage"] for item in progress] == [stage for stage, _label in REVIEW_STAGES]
    assert all(item["stage"] in stages for item in progress)
    assert all(item["cluster_review_id"] == "cluster-1" for item in progress)
    assert all(item["repository_status"] == "reviewing" for item in progress)
    assert all(item["job_id"] == "job-1" for item in progress)
    assert all(item["review_id"] == report["review_id"] for item in progress)
    impact = next(item for item in progress if item["stage"] == "calculate_impact")
    assert impact["stage_label"] == "Calculating cross-repository impact"
    plain = progress_event("calculate_impact", "r", "j")
    assert plain["stage_label"] == "Finding affected services"
    assert "cluster_review_id" not in plain
    assert report["gate"]["decision"] in {"pass", "warn", "block", "incomplete"}


def _assert_counters(payload: dict) -> None:
    repos = payload["repositories"]
    counters = payload["counters"]
    assert counters == aggregate_counters(repos)
    assert counters["repository_total"] == payload["repository_total"]
    assert counters["repositories_running"] == len(payload["running"])
    assert all(item["status"] != "pass" for item in repos)
    assert all(item.get("decision") != "pass" for item in repos if item["status"] == "no_change")


def test_timeframe_selects_each_repository_history_and_reviews_the_existing_range(tmp_path: Path) -> None:
    gateway = _git_repo(tmp_path, "gateway")
    auth = _git_repo(tmp_path, "auth")
    _dated(gateway, "2024-01-15T12:00:00+00:00", "january")
    _dated(auth, "2024-03-15T12:00:00+00:00", "march")
    source = {"kind": "timeframe", "since": "2024-01-01", "until": "2024-01-31"}
    settings = _settings(tmp_path)
    selected = detect_repository_change(gateway, source, settings)
    skipped = detect_repository_change(auth, source, settings)
    assert selected["change_detected"] is True
    assert selected["commit_count"] == 1
    assert selected["commits"][0]["subject"] == "january"
    assert selected["head_ref"]
    assert selected["base_ref"]
    assert skipped["change_detected"] is False
    assert skipped["error_summary"] == ""
    seen: dict[str, dict] = {}

    def review(_member: dict, child: dict) -> dict:
        seen["source"] = child["source"]
        return _report("rev-1", "warn", ["gateway"])

    record = new_cluster_review(
        cluster_id="demo",
        cluster_label="Demo",
        source=source,
        members=[{"service_id": "gateway", "repository": "gateway", "repository_path": str(gateway)}],
    )
    execute_cluster_review(
        record,
        settings=settings,
        body={"source": source, "mode": "fast", "verify": False, "review_scope": "service_cluster", "cluster_id": "demo"},
        publish=lambda *_args: None,
        review=review,
    )
    assert seen["source"]["kind"] == "range"
    assert seen["source"]["base_ref"] == selected["base_ref"]
    assert seen["source"]["head_ref"] == selected["head_ref"]


def test_commit_that_belongs_to_one_repository_is_no_change_in_the_others(tmp_path: Path) -> None:
    origin = _git_repo(tmp_path, "gateway")
    other = _git_repo(tmp_path, "auth")
    (origin / "README.md").write_text("changed\n", encoding="utf-8")
    _commit(origin, "gateway only")
    sha = subprocess.run(["git", "-C", str(origin), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    source = {"kind": "commit", "commit": sha, "repository": str(origin)}
    settings = _settings(tmp_path)
    missing = detect_repository_change(other, source, settings, required=False)
    assert missing["ok"] is True
    assert missing["change_detected"] is False
    assert missing["error_summary"] == ""
    present = detect_repository_change(origin, source, settings, required=True)
    assert present["ok"] is True
    assert present["change_detected"] is True
    assert present["changed_file_count"] == 1
    assert _is_origin({"service_id": "uocp-api-gateway", "repository": "hvrcorp_uocp_api_gateway", "repository_path": str(origin)}, str(origin))
    assert not _is_origin({"service_id": "uocp-auth-service", "repository": "hvrcorp_uocp_auth_service", "repository_path": str(other)}, str(origin))


def _record(tmp_path: Path) -> dict:
    return new_cluster_review(
        cluster_id="demo",
        cluster_label="Demo",
        source={"kind": "working_tree"},
        members=[
            {"service_id": "repo-a", "repository": "repo-a"},
            {"service_id": "repo-b", "repository": "repo-b"},
            {"service_id": "repo-c", "repository": "repo-c"},
        ],
    )


def _body() -> dict:
    return {"source": {"kind": "working_tree"}, "mode": "fast", "verify": False, "review_scope": "service_cluster", "cluster_id": "demo"}


def _detect(changed: dict[str, int]):
    def detect(member: dict, _source: dict) -> dict:
        count = changed.get(member["service_id"], 0)
        return {"ok": True, "change_detected": count > 0, "changed_file_count": count, "error_code": "", "error_summary": ""}

    return detect


def _report(review_id: str, decision: str, affected: list[str]) -> dict:
    return {
        "review_id": review_id,
        "gate": {"decision": decision, "reason_codes": []},
        "gate_validation": {"status": "validated"},
        "review_scope": {"affected_member_services": affected},
    }


def _settings(root: Path) -> Settings:
    output = root / "output"
    output.mkdir(exist_ok=True)
    repos = root / "repos.txt"
    if not repos.exists():
        repos.write_text("repo\n", encoding="utf-8")
    return Settings(root=root, repos_file=repos, output_dir=output, github_token="", ghe_token="")


def _git_repo(root: Path, name: str) -> Path:
    repo = root / name
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _commit(repo, "base")
    return repo


def _dated(repo: Path, when: str, message: str) -> None:
    (repo / "README.md").write_text(message + "\n", encoding="utf-8")
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Nami",
        "GIT_AUTHOR_EMAIL": "nami@example.com",
        "GIT_COMMITTER_NAME": "Nami",
        "GIT_COMMITTER_EMAIL": "nami@example.com",
        "GIT_AUTHOR_DATE": when,
        "GIT_COMMITTER_DATE": when,
    }
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True, text=True, env=env)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", message], check=True, capture_output=True, text=True, env=env)


def _commit(repo: Path, message: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", message)


def _git(repo: Path, *args: str) -> None:
    env = {**os.environ, "GIT_AUTHOR_NAME": "Nami", "GIT_AUTHOR_EMAIL": "nami@example.com", "GIT_COMMITTER_NAME": "Nami", "GIT_COMMITTER_EMAIL": "nami@example.com"}
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=env)
