"""Service-cluster review scope stays on the existing Boundary Gate path."""

from __future__ import annotations

import io
import os
import subprocess
import zipfile
from pathlib import Path

import pytest

from dep_intel.api import ReviewRunBody, ReviewSourceBody
from dep_intel.config import Settings
from dep_intel.cross_repo_graph import index_snapshot
from dep_intel.graph_models import CODE_SYMBOL, GraphNode, Partition, symbol_key
from dep_intel.review_engine import ReviewEngine, _cache_key
from dep_intel.review_export import build_zip, canonical_export, pdf_lines
from dep_intel.review_gate_validation import gate_input_hash, validate_gate
from dep_intel.review_scope import ReviewScopeError, confirm_pull_request_origin, resolve_review_scope
from dep_intel.review_store import render_markdown


def test_omnichannel_declared_membership_matches_repos_txt() -> None:
    from dep_intel.review_scope import display_cluster_label
    from dep_intel.service_clusters import membership_source_label
    from dep_intel.sources import PROJECT_ROOT, load_repos

    repos_file = PROJECT_ROOT / "repos.txt"
    if not repos_file.is_file():
        cluster = {"cluster_id": "omnichannel", "label": "omnichannel", "membership_source": "declared", "services": ["uocp-api-gateway", "uocp-auth-service"]}
        assert display_cluster_label(cluster) == "Omnichannel"
        assert membership_source_label("declared") == "Declared in repos.txt"
        pytest.skip("repos.txt is local and is not present in CI")
    settings = Settings(root=PROJECT_ROOT, repos_file=repos_file, output_dir=PROJECT_ROOT / "output", github_token="", ghe_token="")
    members = sorted(repo.service_id for repo in load_repos(settings) if repo.cluster == "omnichannel")
    assert members == [
        "uocp-analytics-service",
        "uocp-api-gateway",
        "uocp-audit-service",
        "uocp-auth-service",
        "uocp-billing-service",
        "uocp-campaign-service",
        "uocp-contracts",
        "uocp-email-service",
        "uocp-notification-service",
        "uocp-platform",
        "uocp-reporting-service",
        "uocp-routing-service",
        "uocp-sms-service",
        "uocp-tenant-service",
        "uocp-voice-service",
        "uocp-webhook-service",
        "uocp-webrtc-service",
        "uocp-whatsapp-service",
    ]
    cluster = {"cluster_id": "omnichannel", "label": "omnichannel", "membership_source": "declared", "services": members}
    assert display_cluster_label(cluster) == "Omnichannel"
    assert membership_source_label("declared") == "Declared in repos.txt"


def test_review_body_defaults_to_repository_scope() -> None:
    body = ReviewRunBody(source=ReviewSourceBody())
    assert body.review_scope == "repository"
    assert body.cluster_id == ""


def test_repository_scope_is_the_default_and_explicit_repository_matches_it(tmp_path: Path) -> None:
    repo, engine, _snapshot = _ready(tmp_path)
    omitted = _run(engine, repo)
    explicit = _run(engine, repo, review_scope="repository", cluster_id="ignored")
    assert omitted["review_scope"]["mode"] == "repository"
    assert omitted["review_scope"]["label"] == "Individual repository"
    assert "cluster_id" not in omitted["review_scope"]
    assert explicit["gate"]["decision"] == omitted["gate"]["decision"]
    assert explicit["review_scope"]["mode"] == "repository"
    assert [item.get("service_id") for item in explicit["impact"]["ranked_candidates"]] == [item.get("service_id") for item in omitted["impact"]["ranked_candidates"]]


def test_declared_cluster_review_limits_candidates_and_keeps_one_gate(tmp_path: Path) -> None:
    repo, engine, _snapshot = _ready(tmp_path)
    report = _run(engine, repo, review_scope="service_cluster", cluster_id="uocp", service="auth")
    assert report["gate"]["decision"] in {"pass", "warn", "block", "incomplete"}
    assert report["review_scope"]["mode"] == "service_cluster"
    assert report["review_scope"]["cluster_id"] == "uocp"
    assert report["review_scope"]["origin_service_id"] == "auth"
    assert report["review_scope"]["membership_source"] == "declared"
    assert report["review_scope"]["affected_member_services"] == ["auth", "gateway"]
    assert "quiet" in report["review_scope"]["unaffected_member_services"]
    assert report["review_scope"]["unaffected_note"] == "No evidenced impact found"
    services = [item.get("service_id") for item in report["impact"]["ranked_candidates"]]
    assert services == ["gateway"]
    assert report["impact"]["out_of_scope_targets"] == [{"service_id": "outside", "reason": "Outside selected Service Cluster"}]
    assert all(item.get("service_id") != "outside" for item in report["findings"])
    finding_services = {item.get("service_id") for item in report["findings"]}
    assert "quiet" not in finding_services
    assert "outside" not in finding_services
    assert not any(item.get("service_id") == "quiet" for item in report["impact"]["ranked_candidates"])
    assert all("quiet" not in str(item.get("title") or "") for item in report["findings"])
    assert any(str(item.get("title") or "").startswith("gateway — ") for item in report["findings"])
    assert "1 evidenced target exists outside the selected Service Cluster." in report["summary"]["text"]
    assert "6 of" not in report["summary"]["text"]
    assert report["review_scope"]["affected_member_count"] == 2
    assert report["review_scope"]["member_count"] == 3
    again = validate_gate(report)
    assert again["expected_decision"] == report["gate"]["decision"]
    assert again["reproducibility"]["gate_input_hash"] == report["gate_validation"]["reproducibility"]["gate_input_hash"]


def test_unknown_cluster_and_origin_are_rejected(tmp_path: Path) -> None:
    repo, engine, _snapshot = _ready(tmp_path)
    with pytest.raises(ReviewScopeError) as unknown:
        _run(engine, repo, review_scope="service_cluster", cluster_id="missing", service="auth")
    assert unknown.value.code == "unknown_cluster"
    with pytest.raises(ReviewScopeError) as origin:
        _run(engine, repo, review_scope="service_cluster", cluster_id="uocp", service="billing")
    assert origin.value.code == "origin_not_in_cluster"
    with pytest.raises(ReviewScopeError) as invalid:
        _run(engine, repo, review_scope="workspace")
    assert invalid.value.code == "invalid_review_scope"
    bare = ReviewEngine(_settings(tmp_path), cluster=None)
    with pytest.raises(ReviewScopeError) as missing_snapshot:
        bare.run({"source": {"kind": "working_tree", "repository": "repo"}, "review_scope": "service_cluster", "cluster_id": "uocp", "verify": False, "config": _config()})
    assert missing_snapshot.value.code == "cluster_snapshot_unavailable"


def test_cache_key_separates_repository_and_cluster_scope() -> None:
    resolved = {"base_sha": "a", "head_sha": "b", "diff_text": "diff"}
    config = {"config_hash": "cfg"}
    repository = _cache_key(resolved, config, "g", "fast", False, False)
    same = _cache_key(resolved, config, "g", "fast", False, False, review_scope="repository", cluster_id="")
    cluster_a = _cache_key(resolved, config, "g", "fast", False, False, review_scope="service_cluster", cluster_id="uocp")
    cluster_b = _cache_key(resolved, config, "g", "fast", False, False, review_scope="service_cluster", cluster_id="other")
    assert repository == same
    assert repository != cluster_a
    assert cluster_a != cluster_b


def test_pull_request_outside_the_cluster_is_rejected() -> None:
    snapshot = _snapshot()
    scope = resolve_review_scope(snapshot, "service_cluster", "uocp", "auth")
    settings = Settings(root=Path("."), repos_file=Path("repos.txt"), output_dir=Path("output"), github_token="", ghe_token="")
    with pytest.raises(ReviewScopeError) as exc:
        confirm_pull_request_origin(
            scope,
            snapshot,
            settings,
            {"kind": "github_pr", "pr": {"url": "https://github.com/acme/billing/pull/4"}},
            {},
        )
    assert exc.value.code == "origin_not_in_cluster"
    kept = confirm_pull_request_origin(
        scope,
        snapshot,
        settings,
        {"kind": "github_pr", "pr": {"url": "https://github.com/acme/auth/pull/4"}},
        {},
    )
    assert kept["origin_service_id"] == "auth"
    assert kept["cluster_id"] == "uocp"


def test_gate_fingerprint_distinguishes_scope_without_using_labels() -> None:
    base = {"change": {"graph_alignment": "compatible", "base_sha": "a", "head_sha": "b", "graph_version": "g"}, "findings": [], "verification": [], "diagnostics": [], "gate": {"decision": "pass"}}
    repository = {**base, "review_scope": {"mode": "repository", "label": "Individual repository", "origin_service_id": "auth"}}
    cluster = {**base, "review_scope": {"mode": "service_cluster", "cluster_id": "uocp", "cluster_label": "UOCP service cluster"}}
    renamed = {**base, "review_scope": {"mode": "service_cluster", "cluster_id": "uocp", "cluster_label": "other label"}}
    other = {**base, "review_scope": {"mode": "service_cluster", "cluster_id": "other", "cluster_label": "UOCP service cluster"}}
    historical = dict(base)
    assert gate_input_hash(repository) != gate_input_hash(cluster)
    assert gate_input_hash(cluster) == gate_input_hash(renamed)
    assert gate_input_hash(cluster) != gate_input_hash(other)
    assert gate_input_hash(historical) != gate_input_hash(repository)
    assert validate_gate(cluster)["reproducibility"]["gate_input_hash"] == gate_input_hash(cluster)
    assert validate_gate(cluster)["expected_decision"] == validate_gate(repository)["expected_decision"] == "pass"


def test_markdown_groups_ranked_candidates_without_dropping_them() -> None:
    report = _export_report({
        "mode": "service_cluster",
        "cluster_label": "UOCP service cluster",
        "membership_source": "declared",
        "membership_source_label": "Declared in repos.txt",
        "origin_service_id": "auth",
        "member_count": 3,
        "affected_member_count": 2,
    })
    report["impact"] = {
        "affected_services": ["gateway"],
        "ranked_candidates": [
            {"service_id": "gateway", "file_path": "common/runtime.py", "evidence_origin": "structural_only", "structural_score": 0.5, "temporal_score": 0, "hybrid_score": 0.4, "statement": "Structurally reachable."},
            {"service_id": "gateway", "file_path": "app.py", "evidence_origin": "temporal_only", "structural_score": 0, "temporal_score": 0.2, "hybrid_score": 0.1, "statement": "Historical association."},
        ],
    }
    text = render_markdown(report)
    assert "### gateway" in text
    assert "common/runtime.py" in text
    assert "app.py" in text
    assert "2 ranked candidates across 1 service." in text
    assert text.index("common/runtime.py") < text.index("app.py")


def test_exports_reuse_stored_scope_and_old_reports_stay_repository() -> None:
    historical = _export_report(None)
    markdown = render_markdown(historical)
    assert "Review scope: Individual repository" in markdown
    assert "review_scope" not in canonical_export(historical)
    current = _export_report({
        "mode": "service_cluster",
        "label": "Service Cluster",
        "cluster_id": "uocp",
        "cluster_label": "UOCP service cluster",
        "membership_source": "declared",
        "membership_source_label": "Declared in repos.txt",
        "origin_service_id": "auth",
        "member_count": 16,
        "affected_member_count": 6,
        "member_services": [],
        "out_of_scope_targets": [],
    })
    text = render_markdown(current)
    assert "Review scope: Service Cluster" in text
    assert "Service cluster: UOCP service cluster" in text
    assert "Membership source: Declared in repos.txt" in text
    assert "Evidenced impact: 6 of 16 services" in text
    assert "Analysis boundary: UOCP service cluster · 16 declared services." in text
    exported = canonical_export(current)
    assert exported["review_scope"]["cluster_id"] == "uocp"
    pdf = "\n".join(pdf_lines(exported))
    assert "Review scope: Service Cluster" in pdf
    assert "Evidenced impact: 6 / 16 services" in pdf
    assert "Membership source: Declared in repos.txt" in pdf
    assert "Cluster members: 16" in pdf
    archive = zipfile.ZipFile(io.BytesIO(build_zip("abc", exported, current)))
    names = archive.namelist()
    assert any(name.endswith("metadata/review-scope.json") for name in names)
    assert archive.read(next(name for name in names if name.endswith("metadata/review-scope.json"))).decode("utf-8").count("uocp") >= 1


def _run(engine: ReviewEngine, repo: Path, **extra) -> dict:
    body = {
        "source": {"kind": "working_tree", "repository": str(repo)},
        "service": extra.pop("service", "auth"),
        "verify": False,
        "model": {"enabled": False},
        "config": _config(),
    }
    body.update(extra)
    return engine.run(body)


def _ready(tmp_path: Path):
    repo = _repo(tmp_path)
    (repo / "src").mkdir()
    (repo / "src" / "app.js").write_text("function ok() { return 1 }\n", encoding="utf-8")
    _commit(repo, "base")
    (repo / "src" / "app.js").write_text("function ok() { return 2 }\n", encoding="utf-8")
    snapshot = _snapshot()

    class Cluster:
        def current(self):
            return snapshot

        def previous(self):
            return None

        def hybrid_impact(self, _body):
            return 200, {
                "graph_version": "g",
                "diagnostics": [],
                "candidates": [
                    _candidate("gateway", "common/runtime.py", 0.4),
                    _candidate("outside", "other.py", 0.9),
                ],
            }

    return repo, ReviewEngine(_settings(tmp_path), cluster=Cluster()), snapshot


def _snapshot():
    auth = _node("auth", "ok", "src/app.js")
    gateway = _node("gateway", "runtime", "common/runtime.py")
    outside = _node("outside", "other", "other.py")
    billing = _node("billing", "bill", "bill.py")
    quiet = _node("quiet", "quiet", "quiet.py")
    parts = {}
    for node in (auth, gateway, outside, billing, quiet):
        part = Partition(service_id=node.service_id, repository=node.service_id, workspace_head="head")
        part.nodes[node.key] = node
        parts[node.service_id] = part
    return index_snapshot(
        version="g",
        generated_at="2024-06-01T00:00:00+00:00",
        partitions=parts,
        overlay={},
        bridges=[],
        diagnostics=[],
        fingerprints=[],
        declared_clusters=[{"id": "uocp", "services": ["auth", "gateway", "quiet"]}],
    )


def _candidate(service: str, path: str, score: float) -> dict:
    return {
        "service_id": service,
        "file_path": path,
        "hybrid_score": score,
        "evidence_origin": "structural_only",
        "structural": {"score": score, "path": []},
        "temporal": {"score": 0},
        "key": {"repository": service},
    }


def _export_report(scope):
    report = {
        "review_id": "abc",
        "module_version": "1.0",
        "gate": {"decision": "warn", "blocking_findings": [], "warning_findings": [], "review_targets": []},
        "change": {"repository": "auth", "service_id": "auth", "base_sha": "a", "head_sha": "b"},
        "summary": {"text": "WARN."},
        "impact": {"ranked_candidates": [], "affected_services": []},
        "findings": [],
        "verification": [],
        "policies": [],
        "diagnostics": [],
        "limitations": [],
    }
    if scope is not None:
        report["review_scope"] = scope
    return report


def _config() -> dict:
    return {"policies": [], "verification": {"enabled": False, "commands": []}, "gate": {}}


def _node(service: str, name: str, file: str) -> GraphNode:
    qualified = f"{file}::{name}"
    return GraphNode(key=symbol_key(service, file, qualified, "Function"), category=CODE_SYMBOL, service_id=service, name=name, qualified_name=qualified, file_path=file, line_start=1, line_end=3, kind="Function")


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
