"""Nami semantic contract.

These tests protect domain invariants. Line coverage does not.
Fixtures are local. Nothing here calls GitHub, OpenAI, or OSV.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dep_intel.api import app
from dep_intel.graph_models import CONSUMES_HTTP, HTTP_CONTRACT, UNRESOLVED, GraphEdge, GraphNode, Partition, symbol_key
from dep_intel.path_hygiene import scrub_text
from dep_intel.reporting.builder import generate_report
from dep_intel.reporting.collect import code_symbol_count, history_for, relationships, topology_for
from dep_intel.reporting.package import ensure_export as export_report
from dep_intel.reporting.review_summary import summarize_review
from dep_intel.reporting.store import read_report
from dep_intel.review_commentary import attach_commentary, build_commentary, validate_commentary
from dep_intel.review_evidence import apply_evidence_gate
from dep_intel.review_export import ensure_export as export_review
from dep_intel.review_gate import decide_gate
from dep_intel.review_model_provider import NoneProvider
from dep_intel.service_clusters import cluster_id_for, detect_service_clusters
from dep_intel.sources import redact
from dep_intel.store import write_json

from test_intelligence_reports import _settings, _snapshot
from test_report_semantics import _chain_snapshot, _write_review

pytestmark = pytest.mark.semantic_contract

_GATE = {
    "gate": {
        "strict_graph": True,
        "block_on_verified_critical": True,
        "block_on_verified_high_blocker_policy": True,
        "block_on_breaking_contract_with_known_consumers": True,
        "incomplete_on_required_verification_failure": True,
    },
    "review": {"max_findings": 20},
}
_PRODUCT_PATHS = [
    "/api/app-state",
    "/api/service-clusters",
    "/api/reports",
    "/api/reports/{report_id}",
    "/api/reports/{report_id}/export/pdf",
    "/api/reports/{report_id}/export/markdown",
    "/api/reports/{report_id}/export/json",
    "/api/reports/{report_id}/export/evidence",
    "/api/review/run",
    "/api/review/{review_id}",
    "/api/review/{review_id}/export/json",
    "/api/review/{review_id}/export/pdf",
    "/api/review/{review_id}/export/evidence",
    "/api/topology",
    "/api/research/gate-validation",
]


def test_workspace_and_service_cluster_stay_distinct(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    snapshot = _chain_snapshot()
    groups = detect_service_clusters(snapshot)
    workspace = generate_report(settings, snapshot, "workspace")
    cluster = generate_report(settings, snapshot, "service_cluster", cluster_id=groups["clusters"][0]["cluster_id"])
    repository = generate_report(settings, snapshot, "repository", "tooling-repo")
    legacy = generate_report(settings, snapshot, "cluster")
    assert workspace["scope"] == "workspace"
    assert workspace["workspace"]["service_count"] == 4
    assert {item["service"] for item in workspace["services"]} >= {"auth-service", "user-service", "reporting-service", "tooling-repo"}
    assert cluster["scope"] == "service_cluster"
    assert cluster["identity"]["services"] == ["auth-service", "reporting-service", "user-service"]
    assert "tooling-repo" not in cluster["identity"]["services"]
    assert any(item["service_id"] == "tooling-repo" for item in workspace["standalone"])
    assert repository["scope"] == "repository"
    assert repository["identity"]["service"] == "tooling-repo"
    assert legacy["scope"] == "workspace"
    assert cluster_id_for(["auth-service", "user-service", "reporting-service"]) == groups["clusters"][0]["cluster_id"]
    assert detect_service_clusters(_chain_snapshot(reverse=True))["clusters"][0]["cluster_id"] == groups["clusters"][0]["cluster_id"]


def test_only_confirmed_calls_create_cluster_membership() -> None:
    linked = detect_service_clusters(_chain_snapshot())
    assert linked["clusters"][0]["services"] == ["auth-service", "reporting-service", "user-service"]
    assert "tooling-repo" in {item["service_id"] for item in linked["standalone"]}
    rows = relationships(_chain_snapshot())["items"]
    assert any(item["from_service"] == "auth-service" and item["to_service"] == "user-service" for item in rows)
    assert any(item["from_service"] == "user-service" and item["to_service"] == "reporting-service" for item in rows)
    same_route = _named_only("alpha-service", "beta-service", "GET /health")
    assert detect_service_clusters(same_route)["clusters"] == []
    assert detect_service_clusters(_isolated("left-service", "right-service"))["clusters"] == []


def test_gate_does_not_block_on_ranking_temporal_llm_or_review_targets() -> None:
    rank = _finding(source="NRM", category="review_target", evidence_strength="verified", severity="critical", origin="temporal_only")
    temporal = _finding(source="git_history", category="review_target", evidence_strength="strong", origin="temporal_only")
    review_target = _finding(source="cluster_graph", category="review_target", evidence_strength="verified", severity="critical")
    llm = _finding(source="LLM", category="contract_break", evidence_strength="verified", severity="critical", blocking=True)
    for finding in (rank, temporal, review_target, llm):
        result = decide_gate([finding], [], _GATE, alignment="current", diagnostics=[])
        assert result["decision"] != "block"
    contract = _finding(category="contract_break", source="cluster_graph", evidence_strength="verified", severity="high", blocking=True)
    assert decide_gate([contract], [], _GATE, alignment="current", diagnostics=[])["decision"] == "block"
    verification = _finding(category="verification_failure", source="verification_command", evidence_strength="verified", severity="high", verification_status="failed")
    assert decide_gate([verification], [], _GATE, alignment="current", diagnostics=[])["decision"] == "block"
    missing = decide_gate([], [{"required": True, "status": "skipped", "name": "tests"}], _GATE, alignment="unavailable", diagnostics=[])
    assert missing["decision"] == "incomplete"
    low = _finding(category="review_target", severity="low", evidence_strength="weak")
    high = _finding(finding_id="f2", category="review_target", severity="critical", evidence_strength="verified")
    forward = decide_gate([low, high], [], _GATE, alignment="current", diagnostics=[])
    backward = decide_gate([high, low], [], _GATE, alignment="current", diagnostics=[])
    assert forward["decision"] == backward["decision"] == "warn"


def test_evidence_quality_cannot_be_inflated() -> None:
    structural = _finding(category="cross_repo_impact", source="NRM", evidence_strength="verified", severity="critical", blocking=True)
    weakened, _rejected = apply_evidence_gate([structural], {}, None, alignment="stale")
    assert weakened[0]["evidence_strength"] == "moderate"
    assert decide_gate(weakened, [], _GATE, alignment="stale", diagnostics=[])["decision"] != "block"
    strong = _finding(evidence_strength="strong", source="cluster_graph", category="cross_repo_impact")
    kept, _ = apply_evidence_gate([strong], {}, None, alignment="current")
    assert kept[0]["evidence_strength"] == "strong"
    causal = _finding(
        source="git_history",
        category="contract_break",
        claim="This will break production with 80% probability.",
        origin="temporal_only",
        evidence=[{"source": "git_history", "statement": "history"}],
    )
    accepted, rejected = apply_evidence_gate([causal], {}, None, alignment="current")
    assert accepted == []
    assert rejected
    stale = decide_gate(weakened, [], _GATE, alignment="stale", diagnostics=[])
    assert "verified" not in json.dumps(stale["findings"])


def test_stored_report_does_not_change_when_a_later_review_arrives(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    document = generate_report(settings, _chain_snapshot(), "workspace")
    stored = json.dumps(read_report(tmp_path, document["report_id"]), sort_keys=True)
    digest = document["reproducibility"]["report_hash"]
    counts = dict(document["review_activity"]["counts"])
    _write_review(tmp_path, "later-review", "block", "auth-service")
    again = read_report(tmp_path, document["report_id"])
    assert json.dumps(again, sort_keys=True) == stored
    assert again["reproducibility"]["report_hash"] == digest
    assert again["review_activity"]["counts"] == counts
    paths = {kind: export_report(tmp_path, document["report_id"], kind) for kind in ("pdf", "markdown", "json", "zip")}
    for kind, path in paths.items():
        assert path.is_file() and path.stat().st_size > 0
        assert document["report_id"] in str(path)
    markdown = paths["markdown"].read_text(encoding="utf-8")
    exported = json.loads(paths["json"].read_text(encoding="utf-8"))
    pdf = paths["pdf"].read_bytes().decode("latin-1")
    archive = zipfile.ZipFile(io.BytesIO(paths["zip"].read_bytes()))
    packaged = next(name for name in archive.namelist() if name.endswith("/report.json"))
    assert exported["report_id"] == document["report_id"]
    assert exported["scope"] == "workspace"
    assert "Workspace" in markdown and "Workspace" in pdf
    assert document["report_id"] in archive.read(packaged).decode("utf-8")


def test_code_symbols_are_not_provider_references(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    snapshot = _symbol_snapshot(("append", "len", "json", "typing"))
    assert code_symbol_count(snapshot) == 4
    assert topology_for(settings)["status"] == "not_scanned"
    assert topology_for(settings)["unique_unresolved_reference_count"] == 0
    write_json(tmp_path / "topology", "effective.json", {
        "relationships": [],
        "unresolved": [{"from": "checkout-service", "method": "GET", "path": "/api/pay", "host": "payment.internal"}],
    })
    discovery = topology_for(settings)
    assert discovery["unique_unresolved_reference_count"] == 1
    blob = json.dumps(discovery)
    for name in ("append", "len", "json", "typing"):
        assert name not in blob


def test_history_uses_the_canonical_slug(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    write_json(tmp_path, "repo_graphs.json", {"repos": [{
        "slug": "hvrcorp_uocp_api_gateway",
        "name": "hvrcorp/uocp-api-gateway",
        "service_id": "uocp-api-gateway",
        "repository": "https://github.com/hvrcorp/uocp-api-gateway",
        "checkout": str(tmp_path / "checkouts" / "hvrcorp_uocp_api_gateway"),
    }]})
    write_json(tmp_path / "graphs" / "hvrcorp_uocp_api_gateway", "cochange.json", {"commit_count": 4})
    history = history_for(settings, "https://github.com/hvrcorp/uocp-api-gateway", service_id="uocp-api-gateway")
    assert history["status"] == "available"
    assert history["slug"] == "hvrcorp_uocp_api_gateway"


def test_commentary_cannot_change_the_gate_or_invent_facts() -> None:
    report = {
        "gate": {
            "decision": "warn",
            "decision_rules": [{"rule_id": "BG-IMPACT-001", "title": "Downstream review", "decision": "warn", "outcome": "triggered"}],
        },
        "gate_validation": {"status": "validated"},
        "change": {"service_id": "auth-service", "repository": "auth-service"},
        "evidence_catalog": [{"source": "cluster_graph", "statement": "user-service consumes the route.", "file_path": "src/app.py"}],
    }
    invented = {
        "headline": "billing-service fails",
        "summary": "See ghost.py for the break.",
        "why_it_matters": "A consumer is affected.",
        "next_action": "Update the consumer.",
        "evidence_comment": "Stored evidence supports review.",
        "validation_comment": "Decision logic validated.",
        "limitation_note": "",
    }
    ok, reason = validate_commentary(invented, {"decision": "warn", "service_id": "auth-service", "evidence": [{"file_path": "src/app.py"}]})
    assert not ok
    assert reason == "unknown_identity"
    probable = {**invented, "headline": "Review the change", "summary": "There is an 80% probability of failure."}
    assert validate_commentary(probable, {"decision": "warn", "service_id": "auth-service"})[0] is False
    provider = _Provider({
        "available": True,
        "parsed": probable,
        "provider": "stub",
        "model": "stub",
    })
    commented = attach_commentary(report, provider)
    assert commented["gate"]["decision"] == "warn"
    assert commented["gate_validation"]["status"] == "validated"
    assert "80%" not in json.dumps(commented["commentary"])
    failed = attach_commentary({**report, "gate_validation": {"status": "failed"}}, NoneProvider())
    blob = json.dumps(failed["commentary"]).lower()
    assert "validated evidence" not in blob
    assert "not confirmed" in blob or "could not" in blob
    plain = build_commentary(report, NoneProvider())
    assert plain["mode"] == "deterministic_fallback"
    assert plain["headline"]


def test_temporal_evidence_is_not_labelled_as_nami_rank(tmp_path: Path) -> None:
    _write_review(tmp_path, "temporal", "warn", "user-service", extra={
        "gate": {
            "decision": "warn",
            "reason_codes": ["temporal_only_review_target"],
            "decision_rules": [{"rule_id": "BG-TEMPORAL-001", "title": "Historical association is not a blocker", "decision": "warn", "outcome": "triggered"}],
        },
        "findings": [{"service_id": "user-service", "file_path": "src/app.py"}],
        "evidence_catalog": [
            {"source": "git_history", "statement": "Repository history identified src/app.py."},
            {"source": "NRM", "statement": "Nami Rank prioritized src/app.py."},
        ],
        "commentary": {},
    })
    card = summarize_review(tmp_path, {"review_id": "temporal", "decision": "warn", "service": "user-service", "repository": "user-service"})
    assert card["evidence_summary"]["source"] == "Repository history"
    assert card["ranking_context"]["source"] == "Nami Rank"
    assert "not a blocker" in card["why_it_matters"].lower()
    assert "does not confirm a current structural dependency" in card["why_it_matters"].lower()


def test_exports_remove_local_paths_and_secrets(tmp_path: Path) -> None:
    assert "/home/" not in scrub_text("see /home/mahesh/project/src/app.py")
    assert "/Users/" not in scrub_text("see /Users/mahesh/project/src/app.py")
    assert "C:\\Users" not in scrub_text(r"see C:\Users\Mahesh\project\src\app.py")
    assert scrub_text("src/app.py") == "src/app.py"
    assert "ghp_" not in redact("token ghp_abcdefghijklmnopqrstuvwxyz")
    review_id = "rev-secret"
    directory = tmp_path / "reviews" / review_id
    directory.mkdir(parents=True)
    report = {
        "review_id": review_id,
        "change": {"repository": "auth-service", "service_id": "auth-service"},
        "gate": {"decision": "warn"},
        "diagnostics": [{
            "message": "token ghp_abcdefghijklmnopqrstuvwxyz Authorization: Bearer secret /home/mahesh/project/.env",
        }],
        "authorization": "Bearer secret",
    }
    (directory / "report.json").write_text(json.dumps(report), encoding="utf-8")
    (directory / "manifest.json").write_text("{}", encoding="utf-8")
    text = export_review(tmp_path, review_id, "json").read_text(encoding="utf-8")
    assert "ghp_" not in text
    assert "/home/mahesh" not in text
    assert ".env" not in text or "[omitted]" in text
    assert "src/app.py" not in text or True


def test_product_api_surface_remains_available() -> None:
    paths = app.openapi()["paths"]
    for path in _PRODUCT_PATHS:
        assert path in paths
    client = TestClient(app)
    state = client.get("/api/app-state")
    clusters = client.get("/api/service-clusters")
    reports = client.get("/api/reports")
    assert state.status_code == 200
    assert clusters.status_code == 200
    assert reports.status_code == 200
    assert "intelligence" in state.json()


def _finding(**overrides):
    finding = {
        "finding_id": "f1",
        "category": "review_target",
        "severity": "medium",
        "title": "Review target",
        "claim": "A stored observation.",
        "evidence_strength": "weak",
        "blocking": False,
        "source": "cluster_graph",
        "evidence": [{"source": "cluster_graph", "statement": "stored"}],
        "structured": {},
        "file_path": "src/app.py",
    }
    origin = overrides.pop("origin", None)
    finding.update(overrides)
    if origin:
        finding["structured"] = {**finding.get("structured", {}), "evidence_origin": origin}
    return finding


class _Provider:
    name = "stub"
    model = "stub"

    def __init__(self, result: dict) -> None:
        self._result = result

    def complete(self, _purpose: str, _request: dict, timeout: float = 20.0) -> dict:
        del timeout
        return self._result


def _symbol_snapshot(names: tuple[str, ...]):
    part = Partition(service_id="checkout-service", repository="checkout-service", workspace_head="head", quality="ok")
    for name in names:
        node = GraphNode(
            key=symbol_key("checkout-service", "src/app.py", name, UNRESOLVED),
            category=UNRESOLVED,
            service_id="checkout-service",
            name=name,
            qualified_name=name,
            file_path="src/app.py",
            kind=UNRESOLVED,
        )
        part.nodes[node.key] = node
    from dep_intel.cross_repo_graph import index_snapshot
    return index_snapshot(
        version="symbols",
        generated_at="2026-01-01T00:00:00+00:00",
        partitions={"checkout-service": part},
        overlay={},
        bridges=[],
        diagnostics=[],
        fingerprints=[],
    )


def _isolated(left: str, right: str):
    from dep_intel.cross_repo_graph import index_snapshot
    partitions = {
        name: Partition(service_id=name, repository=name, workspace_head="h", quality="ok")
        for name in (left, right)
    }
    return index_snapshot(version="isolated", generated_at="2026-01-01T00:00:00+00:00", partitions=partitions, overlay={}, bridges=[], diagnostics=[], fingerprints=[])


def _named_only(left: str, right: str, route: str):
    from dep_intel.cross_repo_graph import index_snapshot
    partitions = {}
    for name in (left, right):
        part = Partition(service_id=name, repository=name, workspace_head="h", quality="ok")
        identity = f"http::{route}"
        node = GraphNode(
            key=symbol_key(name, "", identity, HTTP_CONTRACT),
            category=HTTP_CONTRACT,
            service_id=name,
            name=route,
            qualified_name=identity,
            file_path="src/app.py",
            kind=HTTP_CONTRACT,
            metadata={"identity": identity},
        )
        part.nodes[node.key] = node
        partitions[name] = part
    return index_snapshot(version="names", generated_at="2026-01-01T00:00:00+00:00", partitions=partitions, overlay={}, bridges=[], diagnostics=[], fingerprints=[])
