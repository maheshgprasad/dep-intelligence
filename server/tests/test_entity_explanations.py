"""Entity explanations describe stored evidence without changing Gate or rank."""

import json
from pathlib import Path

from dep_intel.reporting.builder import generate_report
from dep_intel.reporting.collect import health_for_repository, topology_for
from dep_intel.reporting.explanations import explain_cross_cluster, explain_relationship, explain_review_entity, explain_unresolved
from dep_intel.reporting.render_markdown import render_markdown
from dep_intel.reporting.review_summary import summarize_review
from dep_intel.store import write_json
from test_intelligence_reports import _settings, _snapshot


def test_relationship_explanation_names_the_consumer_contract_and_provider() -> None:
    text = explain_relationship({
        "from_service": "user-service",
        "to_service": "auth-service",
        "contract": "POST /api/auth/validate",
        "kind": "http",
        "provenance": "extracted",
    })
    assert text == "user-service → auth-service: user-service consumes POST /api/auth/validate. Contract edge, not history."


def test_named_path_is_the_evidence_for_a_structural_file() -> None:
    explained = explain_review_entity({
        "file_path": "common/runtime.py",
        "symbol": "send",
        "service_id": "uocp-api-gateway",
        "evidence_origin": "structural_only",
        "structural": {"path": [
            {"source_name": "validate", "source_file": "common/runtime.py", "relation": "HANDLED_BY", "target_name": "POST /v1/validate"},
            {"relation": "CONSUMES_HTTP", "target_name": "send", "target_file": "common/runtime.py"},
        ]},
    })
    assert explained["kind"] == "structural_review_target"
    assert explained["why_tagged"] == explained["why_selected"]
    assert explained["explanation"] == "validate → POST /v1/validate → send"
    assert explained["inspect"] == "Check common/runtime.py."
    hybrid = explain_review_entity({"file_path": "src/users.ts", "evidence_origin": "hybrid"})
    assert hybrid["kind"] == "hybrid"
    assert hybrid["explanation"] == "Graph path and history both match. Hybrid target."


def test_temporal_file_is_explained_separately_from_the_review(tmp_path: Path) -> None:
    directory = tmp_path / "reviews" / "rev-temporal"
    directory.mkdir(parents=True)
    (directory / "manifest.json").write_text("{}", encoding="utf-8")
    (directory / "report.json").write_text(json.dumps({
        "review_id": "rev-temporal",
        "gate": {
            "decision": "warn",
            "reason_codes": ["temporal_only_review_target"],
            "decision_rules": [{"rule_id": "BG-TEMPORAL-001", "title": "Historical association is not a blocker", "decision": "warn", "outcome": "triggered"}],
        },
        "impact": {"affected_services": ["billing-service"]},
        "findings": [
            {"service_id": "user-service", "file_path": "src/users.ts", "claim": "Historical association."},
            {"service_id": "user-service", "file_path": "src/sessions.ts", "claim": "Another file."},
        ],
        "evidence_catalog": [{"source": "git_history", "statement": "Repository history identified the files.", "file_path": "src/users.ts"}],
        "commentary": {"why_it_matters": "The review as a whole needs attention.", "next_action": "Read the files."},
    }), encoding="utf-8")
    card = summarize_review(
        tmp_path,
        {"review_id": "rev-temporal", "decision": "warn", "service": "user-service"},
        members={"user-service"},
    )
    assert "this evidence alone does not confirm a current structural dependency" in card["why_it_matters"].lower()
    users = next(item for item in card["entity_explanations"] if item["label"] == "src/users.ts")
    sessions = next(item for item in card["entity_explanations"] if item["label"] == "src/sessions.ts")
    billing = next(item for item in card["entity_explanations"] if item["label"] == "billing-service")
    assert users["kind"] == "temporal_only"
    assert users["explanation"] == "History associates it with the change. Temporal target, not a dependency."
    assert users["evidence_basis"]
    assert users["claim"]
    assert users["inspect"]
    assert sessions["kind"] == "temporal_only"
    assert users["why_selected"] != card["why_it_matters"]
    assert billing["kind"] == "cross_cluster_target"
    assert billing == explain_cross_cluster("billing-service")


def test_named_path_replaces_a_generic_finding(tmp_path: Path) -> None:
    directory = tmp_path / "reviews" / "rev-path"
    directory.mkdir(parents=True)
    (directory / "manifest.json").write_text("{}", encoding="utf-8")
    (directory / "report.json").write_text(json.dumps({
        "review_id": "rev-path",
        "gate": {"decision": "warn", "decision_rules": []},
        "findings": [{"service_id": "gateway", "file_path": "common/runtime.py", "source": "NRM", "claim": "Review target."}],
        "impact": {"ranked_candidates": [{
            "service_id": "gateway",
            "file_path": "common/runtime.py",
            "name": "send",
            "evidence_origin": "structural_only",
            "structural": {"path": [
                {"source_name": "validate", "relation": "HANDLED_BY", "target_name": "POST /v1/validate"},
                {"relation": "CONSUMES_HTTP", "target_name": "common/runtime.py::send"},
            ]},
        }]},
        "commentary": {"why_it_matters": "Review the change.", "next_action": "Read the file."},
    }), encoding="utf-8")
    card = summarize_review(tmp_path, {"review_id": "rev-path", "decision": "warn", "service": "gateway"})
    explained = card["entity_explanations"][0]
    assert explained["explanation"] == "validate → POST /v1/validate → send"


def test_repository_report_explains_the_upstream_edge(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    document = generate_report(settings, _snapshot(), "repository", "user-service")
    upstream = document["relationships"]["upstream"][0]
    assert upstream["explanation"].startswith("user-service → auth-service:")
    assert "POST /api/auth/validate" in upstream["explanation"]
    assert "listUsers" in upstream["explanation"]
    assert "src/users.js" in upstream["explanation"]
    assert "Contract edge, not history." in upstream["explanation"]
    text = render_markdown(document)
    assert "auth-service via POST /api/auth/validate (listUsers, src/users.js)" in text
    assert upstream["explanation"] not in text
    assert document["service"]["explanation"]
    assert document["service"]["explanation"].startswith("user-service: contract edges.")


def test_update_hygiene_names_only_repositories_present_in_the_artifact(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    write_json(tmp_path, "package_updates.json", {
        "meta": {"generated_at": "2026-10-29T11:08:55+00:00", "total_updates": 1},
        "updates": {"major": [], "minor": [{"repo": "other-repo", "package": "left-pad", "from": "1.0.0", "to": "1.1.0"}], "patch": []},
        "summary": {"total": 1},
    })
    named = health_for_repository(settings, "other-repo")
    missing = health_for_repository(settings, "uocp-api-gateway", "uocp-api-gateway")
    assert named["updates"]["status"] == "available"
    assert named["updates"]["finding_count"] == 1
    assert missing["updates"]["status"] == "not_scanned"
    assert "does not name this repository" in missing["updates"]["detail"]


def test_unresolved_provider_is_not_called_a_dependency(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    write_json(tmp_path / "topology", "effective.json", {
        "unresolved": [{"from": "user-service", "host": "payments.internal", "method": "POST", "path": "/charge"}],
        "relationships": [],
    })
    payload = topology_for(settings, {"user-service"})
    text = payload["items"][0]["explanation"]
    assert text == explain_unresolved({"service": "user-service", "name": "payments.internal"})
    assert text == "user-service references payments.internal. Unresolved, not a dependency."
