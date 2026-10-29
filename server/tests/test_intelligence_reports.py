"""Repository and cluster intelligence reports from a published snapshot."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from dep_intel.api import app
from dep_intel.config import Settings
from dep_intel.cross_repo_graph import index_snapshot
from dep_intel.graph_models import CONSUMES_HTTP, HTTP_CONTRACT, UNRESOLVED, GraphEdge, GraphNode, Partition, symbol_key
from dep_intel.reporting.builder import generate_report
from dep_intel.reporting.store import fingerprint, list_reports, save_report
from dep_intel.store import write_json


def test_repository_report_records_identity_contracts_and_hash(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _history(tmp_path, "auth-service", commits=4)
    document = generate_report(settings, _snapshot(), "repository", "auth-service")
    assert document["schema"] == "nami.intelligence.report"
    assert document["schema_version"] == 1
    assert document["scope"] == "repository"
    assert document["identity"]["service"] == "auth-service"
    assert document["identity"]["graph_version"] == "g-current"
    assert document["contracts"]["provided"]
    assert document["contracts"]["provided"][0]["consumers"] == ["user-service"]
    assert document["relationships"]["downstream"]
    assert document["history"]["status"] == "available"
    assert document["health"]["coverage"]["status"] == "not_scanned"
    assert document["limitations"]
    assert document["reproducibility"]["report_hash"].startswith("sha256:")
    assert "Boundary Gate decision" in document["limitations"][0]
    assert fingerprint(document) == document["reproducibility"]["report_hash"]


def test_cluster_report_counts_services_relationships_and_revisions(tmp_path: Path) -> None:
    document = generate_report(_settings(tmp_path), _snapshot(user_quality="stale"), "cluster")
    assert document["scope"] == "cluster"
    assert document["workspace"]["service_count"] == 2
    assert document["workspace"]["relationship_count"] == 1
    assert document["workspace"]["contract_count"] == 1
    assert document["workspace"]["graph_stale"] == 1
    assert document["identity"]["revision_vector"]["auth-service"]["workspace_head"] == "head-auth"
    assert document["executive_summary"]["readiness"] == "limited"
    assert any("out of date" in item.lower() for item in document["limitations"])
    assert any(item["service"] == "user-service" for item in document["recommendations"])


def test_generation_does_not_refresh_rank_or_gate(tmp_path: Path, monkeypatch) -> None:
    def forbidden(*_args, **_kwargs):
        raise AssertionError("report generation must not reanalyze")

    monkeypatch.setattr("dep_intel.cluster_service.ClusterEngine.refresh", forbidden)
    monkeypatch.setattr("dep_intel.review_gate.decide_gate", forbidden)
    monkeypatch.setattr("dep_intel.hybrid_impact.fuse_candidate", forbidden, raising=False)
    document = generate_report(_settings(tmp_path), _snapshot(), "cluster")
    assert document["scope"] == "cluster"


def test_missing_history_is_unavailable_not_a_crash(tmp_path: Path) -> None:
    document = generate_report(_settings(tmp_path), _snapshot(), "repository", "user-service")
    assert document["history"]["status"] == "unavailable"
    cluster = generate_report(_settings(tmp_path), _snapshot(), "cluster")
    assert "user-service" in cluster["history"]["missing"]
    assert "auth-service" in cluster["history"]["missing"]


def test_missing_health_is_not_scanned(tmp_path: Path) -> None:
    document = generate_report(_settings(tmp_path), _snapshot(), "repository", "auth-service")
    assert document["health"]["security"]["status"] == "not_scanned"
    assert document["health"]["coverage"]["status"] == "not_scanned"
    assert document["health"]["packages"]["status"] == "not_scanned"
    assert "clean" not in document["health"]["security"]["detail"].lower()


def test_fingerprint_is_stable_until_content_changes() -> None:
    document = {"schema": "nami.intelligence.report", "scope": "cluster", "executive_summary": {"summary": "same"}, "reproducibility": {}}
    assert fingerprint(document) == fingerprint(json.loads(json.dumps(document)))
    changed = json.loads(json.dumps(document))
    changed["executive_summary"]["summary"] = "different"
    assert fingerprint(document) != fingerprint(changed)


def test_exports_and_package_are_rendered_from_the_stored_report(tmp_path: Path, monkeypatch) -> None:
    settings = _settings(tmp_path)
    document = generate_report(settings, _snapshot(), "repository", "auth-service")
    document["limitations"].append("token ghp_abcdefghijklmnopqrstuvwxyz")
    save_report(settings.output_dir, document, {"report_id": document["report_id"], "scope": "repository", "service_id": "auth-service", "graph_version": "g-current", "created_at": document["generated_at"], "nami_version": "0.1.0", "report_schema_version": 1})
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))
    client = TestClient(app)
    report_id = document["report_id"]
    pdf = client.get(f"/api/reports/{report_id}/export/pdf")
    assert pdf.status_code == 200
    assert pdf.headers["content-type"].startswith("application/pdf")
    assert pdf.content.startswith(b"%PDF")
    assert b"auth-service" in pdf.content
    assert b"Nami Trace" in pdf.content
    exported = client.get(f"/api/reports/{report_id}/export/json")
    assert exported.status_code == 200
    body = exported.json()
    assert body["schema"] == "nami.intelligence.report"
    assert body["scope"] == "repository"
    markdown = client.get(f"/api/reports/{report_id}/export/markdown")
    assert markdown.status_code == 200
    assert "auth-service" in markdown.text
    package = client.get(f"/api/reports/{report_id}/export/evidence")
    assert package.status_code == 200
    archive = zipfile.ZipFile(io.BytesIO(package.content))
    names = archive.namelist()
    assert any(name.endswith("metadata/manifest.json") for name in names)
    assert any(name.endswith("contracts/provided.json") for name in names)
    joined = "\n".join(names)
    assert ".env" not in joined
    assert ".git" not in joined
    assert "worktree" not in joined
    raw = package.content
    assert b"ghp_" not in raw
    manifest = json.loads(archive.read(next(name for name in names if name.endswith("metadata/manifest.json"))))
    assert set(manifest["files"]) == set(names)


def test_listing_is_newest_first_and_skips_corrupt_directories(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    older = generate_report(settings, _snapshot(), "cluster")
    newer = generate_report(settings, _snapshot(), "repository", "auth-service")
    (tmp_path / "reports" / "corrupt").mkdir(parents=True)
    (tmp_path / "reports" / "corrupt" / "manifest.json").write_text("{", encoding="utf-8")
    (tmp_path / "reports" / newer["report_id"] / "manifest.json").write_text(
        json.dumps({"report_id": newer["report_id"], "scope": "repository", "service_id": "auth-service", "graph_version": "g-current", "created_at": "2026-02-02T00:00:00+00:00", "nami_version": "0.1.0", "report_schema_version": 1}),
        encoding="utf-8",
    )
    (tmp_path / "reports" / older["report_id"] / "manifest.json").write_text(
        json.dumps({"report_id": older["report_id"], "scope": "cluster", "service_id": None, "graph_version": "g-current", "created_at": "2026-01-01T00:00:00+00:00", "nami_version": "0.1.0", "report_schema_version": 1}),
        encoding="utf-8",
    )
    listed = list_reports(tmp_path, limit=1)
    assert len(listed) == 1
    assert listed[0]["report_id"] == newer["report_id"]
    assert "services" not in listed[0]
    assert "executive_summary" not in listed[0]
    assert list_reports(tmp_path, limit=5)[0]["report_id"] == newer["report_id"]


def test_api_generate_uses_the_current_snapshot_without_refresh(tmp_path: Path, monkeypatch) -> None:
    settings = _settings(tmp_path)
    snapshot = _snapshot()

    class _Engine:
        def current(self):
            return snapshot

        def refresh(self):
            raise AssertionError("refresh")

    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr("dep_intel.api.get_engine", lambda _settings=None: _Engine())
    monkeypatch.setattr("dep_intel.api.Settings.load", lambda *_args, **_kwargs: settings)
    client = TestClient(app)
    response = client.post("/api/reports", json={"scope": "cluster"})
    assert response.status_code == 200
    body = response.json()
    assert body["scope"] == "cluster"
    assert body["graph_version"] == "g-current"
    mismatch = client.post("/api/reports", json={"scope": "cluster", "graph_version": "other"})
    assert mismatch.status_code == 409


def _settings(tmp_path: Path) -> Settings:
    repos = tmp_path / "repos.txt"
    repos.write_text("auth-service\nuser-service\n", encoding="utf-8")
    return Settings(root=tmp_path, repos_file=repos, output_dir=tmp_path, github_token="", ghe_token="")


def _history(tmp_path: Path, slug: str, commits: int) -> None:
    write_json(tmp_path / "graphs" / slug, "cochange.json", {"commit_count": commits, "coverage": 1})


def _snapshot(user_quality: str = "ok") -> object:
    identity = "http::POST:/api/auth/validate"
    contract = GraphNode(
        key=symbol_key("auth-service", "", identity, HTTP_CONTRACT),
        category=HTTP_CONTRACT,
        service_id="auth-service",
        name="POST /api/auth/validate",
        qualified_name=identity,
        file_path="src/server.js",
        kind=HTTP_CONTRACT,
        metadata={"identity": identity},
    )
    caller = GraphNode(
        key=symbol_key("user-service", "src/users.js", "src/users.js::listUsers", "Function"),
        category="code_symbol",
        service_id="user-service",
        name="listUsers",
        qualified_name="src/users.js::listUsers",
        file_path="src/users.js",
        kind="Function",
    )
    unresolved = GraphNode(
        key=symbol_key("user-service", "", "payment.internal", UNRESOLVED),
        category=UNRESOLVED,
        service_id="user-service",
        name="payment.internal",
        qualified_name="payment.internal",
        file_path="",
        kind=UNRESOLVED,
    )
    auth = Partition(service_id="auth-service", repository="auth-service", workspace_head="head-auth", quality="ok")
    user = Partition(service_id="user-service", repository="user-service", workspace_head="head-user", quality=user_quality)
    auth.nodes[contract.key] = contract
    user.nodes[caller.key] = caller
    user.nodes[unresolved.key] = unresolved
    edge = GraphEdge(caller.key, contract.key, CONSUMES_HTTP, {"status": "extracted"})
    return index_snapshot(
        version="g-current",
        generated_at="2026-01-01T00:00:00+00:00",
        partitions={"auth-service": auth, "user-service": user},
        overlay={},
        bridges=[edge],
        diagnostics=[],
        fingerprints=[],
    )
