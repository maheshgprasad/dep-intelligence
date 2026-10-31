"""Regression tests for the reliability hardening pass."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from dep_intel.api import app
from dep_intel.app_state import build_app_state, cross_service_relationships, service_catalog
from dep_intel.cluster_manifest import ImpactSettings
from dep_intel.cluster_service import _file_allowed, resolve_roots
from dep_intel.config import Settings
from dep_intel.cross_repo_graph import index_snapshot
from dep_intel.graph_models import CODE_SYMBOL, CONSUMES_HTTP, HANDLED_BY, HTTP_CONTRACT, GraphEdge, GraphNode, Partition, symbol_key
from dep_intel.impact import analyze
from dep_intel.review_engine import ReviewEngine
from dep_intel.review_progress import REVIEW_STAGES, progress_event, resolve_review_mode
from dep_intel.review_store import list_reviews


def test_file_allowed_matches_only_the_requested_service() -> None:
    auth = _node("auth-service", "validate", "src/auth/index.ts")
    user = _node("user-service", "listUsers", "src/users.js")
    snapshot = _snapshot([auth, user])
    assert _file_allowed(snapshot, "auth-service", "src/auth/index.ts") is True
    assert _file_allowed(snapshot, "auth-service", "src/users.js") is False
    assert _file_allowed(snapshot, "auth-service", "src/missing.ts") is False
    assert _file_allowed(snapshot, "auth-service", "../secret.ts") is False
    assert _file_allowed(snapshot, "auth-service", "/tmp/src/auth/index.ts") is False
    assert _file_allowed(snapshot, "", "src/auth/index.ts") is True


def test_operational_scenarios_accept_only_http_and_grpc() -> None:
    node = _node("auth-service", "validate", "src/auth/index.ts")
    snapshot = _snapshot([node])
    http = analyze(snapshot, [node.key], mode="operational_failure", settings=ImpactSettings(), scenario="http_unavailable")
    grpc = analyze(snapshot, [node.key], mode="operational_failure", settings=ImpactSettings(), scenario="grpc_unavailable")
    missing = analyze(snapshot, [node.key], mode="operational_failure", settings=ImpactSettings(), scenario="")
    event = analyze(snapshot, [node.key], mode="operational_failure", settings=ImpactSettings(), scenario="event_consumer_unavailable")
    assert not any(item.code == "unsupported_operational" for item in http.diagnostics)
    assert not any(item.code == "unsupported_operational" for item in grpc.diagnostics)
    assert any(item.code == "unsupported_operational" for item in missing.diagnostics)
    assert any(item.code == "unsupported_operational" for item in event.diagnostics)


def test_app_state_stays_lightweight(tmp_path: Path, monkeypatch) -> None:
    output = tmp_path / "out"
    output.mkdir()
    (output / "vulnerabilities.json").write_text(json.dumps({"findings": [{"id": "CVE-2024-0001", "summary": "secret-table"}]}), encoding="utf-8")
    (output / "repo_graphs.json").write_text(json.dumps({"repos": [
        {"slug": "auth", "name": "auth", "checkout": str(tmp_path / "auth")},
        {"slug": "user", "name": "user", "checkout": str(tmp_path / "user")},
    ]}), encoding="utf-8")
    (output / "graphs" / "auth").mkdir(parents=True)
    (output / "graphs" / "auth" / "cochange.json").write_text(json.dumps({"commit_count": 2, "pairs": [{"a": 1}]}), encoding="utf-8")
    settings = Settings(root=tmp_path, repos_file=tmp_path / "repos.txt", output_dir=output, github_token="", ghe_token="")
    summary = {
        "version": "v1",
        "generated_at": "2026-01-01T00:00:00+00:00",
        "services": [{"id": "auth", "quality": "ok", "error": ""}, {"id": "user", "quality": "ok", "error": ""}],
        "contract_count": 0,
        "unresolved_count": 0,
    }
    state = build_app_state(settings, summary, False)
    assert state["intelligence"]["history"]["status"] == "partial"
    assert state["intelligence"]["history"]["ready_services"] == 1
    assert state["intelligence"]["contracts"]["extraction_complete"] is True
    assert state["intelligence"]["contracts"]["count"] == 0
    assert state["health"]["security"]["available"] is True
    encoded = json.dumps(state)
    for forbidden in ("graph_details", "security_markdown", "CVE-2024-0001", "secret-table", "code_review"):
        assert forbidden not in encoded

    monkeypatch.setattr("dep_intel.api.Settings.load", lambda: settings)
    monkeypatch.setattr("dep_intel.api.get_engine", lambda _settings: type("Engine", (), {"summary": lambda self: summary})())
    response = TestClient(app).get("/api/app-state")
    assert response.status_code == 200
    assert "graph_details" not in response.json()


def test_catalog_returns_unique_relative_files_for_one_service() -> None:
    auth = _node("auth-service", "validate", "src/auth/index.ts")
    other = _node("auth-service", "helper", "src/auth/index.ts")
    user = _node("user-service", "listUsers", "src/users.js")
    contract = _contract("auth-service")
    snapshot = _snapshot([auth, other, user, contract])
    catalog = service_catalog(snapshot, "auth-service")
    assert catalog["files"] == [
        {"path": "src/auth/index.ts", "label": "src/auth/index.ts"},
        {"path": "src/server.js", "label": "src/server.js"},
    ]
    assert len({item["path"] for item in catalog["files"]}) == len(catalog["files"])
    assert catalog["contracts"][0]["service_id"] == "auth-service"
    assert service_catalog(snapshot, "missing")["error"] == "unknown_service"
    contract.metadata["identity"] = contract.qualified_name
    by_key, _candidates, error = resolve_roots(snapshot, service="auth-service", symbol="", file="", line=None, contract_id=catalog["contracts"][0]["id"])
    assert error == "" and by_key == [contract.key]
    by_identity, _candidates, error = resolve_roots(snapshot, service="auth-service", symbol="", file="", line=None, contract_id=contract.qualified_name)
    assert error == "" and by_identity == [contract.key]


def test_relationships_are_cross_service_contracts_only() -> None:
    contract = _contract("auth-service")
    caller = _node("user-service", "listUsers", "src/users.js")
    handler = _node("auth-service", "validate", "src/auth/index.ts")
    report = _node("report-job", "total", "report_job.py")
    snapshot = _snapshot(
        [contract, caller, handler, report],
        [
            GraphEdge(caller.key, contract.key, CONSUMES_HTTP, {"status": "extracted", "resolution": "exact", "file": "src/users.js", "line": 6}),
            GraphEdge(contract.key, handler.key, HANDLED_BY, {"status": "extracted"}),
        ],
    )
    payload = cross_service_relationships(snapshot)
    rows = payload["relationships"]
    assert len(rows) == 1
    assert rows[0]["from_service"] == "user-service"
    assert rows[0]["to_service"] == "auth-service"
    assert rows[0]["kind"] == "http"
    assert rows[0]["contract"] == "POST /api/auth/validate"
    assert rows[0]["caller_symbol"] == "listUsers"
    assert rows[0]["caller_file"] == "src/users.js"
    assert rows[0]["evidence"] == "extracted"
    assert rows[0]["resolution"] == "exact"
    assert payload["total"] == 1
    assert payload["clusters"][0]["services"] == ["auth-service", "user-service"]
    assert payload["clusters"][0]["relationships"] == 1
    assert str(payload["clusters"][0]["cluster_id"]).startswith("svc-cluster-")
    assert payload["individuals"] == ["report-job"]


def test_review_progress_is_authoritative_and_monotonic(tmp_path: Path) -> None:
    events = []
    repo = _repo(tmp_path)
    (repo / "src").mkdir()
    (repo / "src" / "app.js").write_text("function ok() { return 1 }\n", encoding="utf-8")
    _commit(repo, "base")
    engine = ReviewEngine(_settings(tmp_path), cluster=None, publish=lambda name, data: events.append((name, data)))
    report = engine.run({"source": {"kind": "working_tree", "repository": "repo"}, "verify": False, "job_id": "job-1", "config": {"policies": [], "verification": {"enabled": False, "commands": []}}})
    progress = [data for name, data in events if name == "review_progress"]
    stages = {stage for stage, _label in REVIEW_STAGES}
    percents = [item["percent"] for item in progress]
    assert percents == sorted(percents)
    assert percents[-1] == 100
    assert all(item["percent"] <= 100 for item in progress)
    assert all(item["stage"] in stages for item in progress)
    assert all(item["review_id"] == report["review_id"] for item in progress)
    assert all(item["job_id"] == "job-1" for item in progress)
    sample = progress_event("calculate_impact", "r", "j")
    assert sample["stage_index"] == 4
    assert sample["percent"] == 44


def test_auto_mode_resolution(tmp_path: Path) -> None:
    assert resolve_review_mode("fast", "stale") == "fast"
    assert resolve_review_mode("exact", "exact") == "exact"
    assert resolve_review_mode("auto", "compatible") == "fast"
    assert resolve_review_mode("auto", "exact") == "fast"
    assert resolve_review_mode("auto", "stale") == "exact"
    repo = _repo(tmp_path)
    (repo / "src").mkdir()
    (repo / "src" / "app.js").write_text("function ok() { return 1 }\n", encoding="utf-8")
    _commit(repo, "base")
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    (repo / "src" / "app.js").write_text("function ok() { return 2 }\n", encoding="utf-8")
    node = _node("auth", "ok", "src/app.js")
    snapshot = _snapshot([node], head=head, service="auth")

    class Cluster:
        def current(self):
            return snapshot

        def previous(self):
            return None

    report = ReviewEngine(_settings(tmp_path), cluster=Cluster()).run({
        "source": {"kind": "working_tree", "repository": "repo"},
        "mode": "auto",
        "service": "auth",
        "verify": False,
        "config": {"policies": [], "verification": {"enabled": False, "commands": []}},
    })
    assert report["requested_mode"] == "auto"
    assert report["resolved_mode"] == "fast"
    assert report["mode"] == "fast"


def test_change_view_stops_before_the_gate(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "src").mkdir()
    (repo / "src" / "app.js").write_text("function ok() { return 1 }\n", encoding="utf-8")
    _commit(repo, "base")
    (repo / "src" / "app.js").write_text("function ok() { return 2 }\n", encoding="utf-8")
    preview = ReviewEngine(_settings(tmp_path), cluster=None).change_view({"source": {"kind": "working_tree", "repository": "repo"}})
    assert preview["changed_files"]
    assert "graph_alignment" in preview
    assert "gate" not in preview
    assert "verification" not in preview
    assert "findings" not in preview


def test_recent_reviews_skip_corrupt_entries_and_omit_evidence(tmp_path: Path) -> None:
    root = tmp_path / "reviews"
    older = root / "old"
    newer = root / "new"
    broken = root / "broken"
    for directory in (older, newer, broken):
        directory.mkdir(parents=True)
    (older / "report.json").write_text(json.dumps({"review_id": "old", "gate": {"decision": "warn"}, "findings": [{}], "change": {"service_id": "auth"}, "impact": {"ranked_candidates": [{"secret": True}]}}), encoding="utf-8")
    (older / "manifest.json").write_text(json.dumps({"review_id": "old", "completed_at": "2024-01-01T00:00:00+00:00"}), encoding="utf-8")
    (newer / "report.json").write_text(json.dumps({"review_id": "new", "gate": {"decision": "pass"}, "findings": [], "change": {"service_id": "user"}}), encoding="utf-8")
    (newer / "manifest.json").write_text(json.dumps({"review_id": "new", "completed_at": "2026-01-01T00:00:00+00:00"}), encoding="utf-8")
    (broken / "report.json").write_text("{", encoding="utf-8")
    os.utime(older, (1, 1))
    os.utime(newer, (2, 2))
    rows = list_reviews(tmp_path, limit=20)
    assert [row["review_id"] for row in rows] == ["new", "old"]
    encoded = json.dumps(rows)
    assert "ranked_candidates" not in encoded
    assert "secret" not in encoded
    assert list_reviews(tmp_path, limit=1)[0]["review_id"] == "new"


def _node(service: str, name: str, file: str) -> GraphNode:
    qualified = f"{file}::{name}"
    return GraphNode(key=symbol_key(service, file, qualified, "Function"), category=CODE_SYMBOL, service_id=service, name=name, qualified_name=qualified, file_path=file, line_start=1, line_end=4, kind="Function")


def _contract(service: str) -> GraphNode:
    identity = "http::POST:/api/auth/validate"
    return GraphNode(key=symbol_key(service, "src/server.js", identity, HTTP_CONTRACT), category=HTTP_CONTRACT, service_id=service, name="POST /api/auth/validate", qualified_name=identity, file_path="src/server.js", line_start=1, line_end=4, kind=HTTP_CONTRACT)


def _snapshot(nodes: list[GraphNode], edges: list[GraphEdge] | None = None, head: str = "head", service: str = "") -> object:
    partitions: dict[str, Partition] = {}
    for node in nodes:
        part = partitions.setdefault(node.service_id, Partition(service_id=node.service_id, repository=node.service_id, workspace_head=head))
        part.nodes[node.key] = node
    return index_snapshot(version="g", generated_at="2026-01-01T00:00:00+00:00", partitions=partitions, overlay={}, bridges=edges or [], diagnostics=[], fingerprints=[])


def _settings(root: Path) -> Settings:
    repos = root / "repos.txt"
    repos.write_text("repo\n", encoding="utf-8")
    output = root / "output"
    output.mkdir(exist_ok=True)
    return Settings(root=root, repos_file=repos, output_dir=output, github_token="", ghe_token="")


def _repo(root: Path) -> Path:
    repo = root / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    return repo


def _commit(repo: Path, message: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", message)


def _git(repo: Path, *args: str) -> None:
    env = {**os.environ, "GIT_AUTHOR_NAME": "Nami", "GIT_AUTHOR_EMAIL": "nami@example.com", "GIT_COMMITTER_NAME": "Nami", "GIT_COMMITTER_EMAIL": "nami@example.com"}
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=env)
