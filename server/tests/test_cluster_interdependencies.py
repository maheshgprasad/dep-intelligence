"""Cluster change-set correlation. Child Gate decisions stay on the child review."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from dep_intel.cluster_interdependencies import (
    analyze_interdependencies,
    copy_summary,
    evidence_zip,
    fold_assessment,
    render_json,
    render_markdown,
    render_pdf,
)
from dep_intel.cluster_review import execute_cluster_review, new_cluster_review
from dep_intel.config import Settings


def _edge(consumer: str, provider: str, contract: str, provenance: str = "confirmed") -> dict:
    method, path = contract.split(" ", 1)
    return {
        "from_service": consumer,
        "to_service": provider,
        "kind": "http",
        "contract": contract,
        "method": method,
        "path": path,
        "provenance": provenance,
    }


def _member(service: str, changed: bool = False, changes: list | None = None, decision: str = "", review_id: str = "") -> dict:
    return {
        "service_id": service,
        "changed": changed,
        "decision": decision,
        "review_id": review_id,
        "contract_changes": changes or [],
    }


def _removed(method: str, path: str, evidence: str = "EV-021", consumers: list | None = None) -> dict:
    return {"kind": "operation_removed", "method": method, "path": path, "breaking": True, "evidence_ids": [evidence], "consumers": consumers or []}


def _added(method: str, path: str, evidence: str = "EV-022") -> dict:
    return {"kind": "operation_added", "method": method, "path": path, "breaking": False, "evidence_ids": [evidence]}


def _consumption(kind: str, provider: str, method: str, path: str, evidence: str) -> dict:
    return {"kind": kind, "role": "consumer", "provider": provider, "method": method, "path": path, "evidence_ids": [evidence]}


def _by(result: dict, classification: str, consumer: str = "") -> dict:
    rows = [item for item in result["items"] if item["classification"] == classification and (not consumer or item["target_service"] == consumer)]
    assert rows, classification
    return rows[0]


def test_provider_break_leaves_unchanged_consumer_broken() -> None:
    result = analyze_interdependencies(
        [
            _member("provider", True, [_removed("POST", "/v1/test")], decision="block", review_id="rev-p"),
            _member("consumer"),
        ],
        [_edge("consumer", "provider", "POST /v1/test")],
        cluster_id="demo",
    )
    item = _by(result, "broken", "consumer")
    assert item["source_service"] == "provider"
    assert item["contract_id"] == "POST /v1/test"
    assert "EV-021" in item["evidence_ids"]
    assert "consumer" in result["unchanged_but_affected_services"]
    assert "provider" in result["changed_services"]
    assert "consumer" not in result["changed_services"]
    assessment = fold_assessment({"code": "clear", "label": "CLEAR", "summary": "1 pass"}, result, "completed")
    assert assessment["label"] == "ATTENTION REQUIRED"
    assert assessment["code"] == "attention_required"


def test_coordinated_removal_is_not_an_unresolved_break() -> None:
    result = analyze_interdependencies(
        [
            _member("provider", True, [_removed("POST", "/v1/test")], decision="block", review_id="rev-p"),
            _member("consumer", True, [_consumption("consumption_removed", "provider", "POST", "/v1/test", "EV-030")], decision="pass", review_id="rev-c"),
        ],
        [_edge("consumer", "provider", "POST /v1/test")],
    )
    assert _by(result, "relationship_removed")["classification"] == "relationship_removed"
    assert not any(item["classification"] == "broken" for item in result["items"])
    assert result["items"][0]["child_review_ids"]


def test_version_migration_is_coordinated() -> None:
    result = analyze_interdependencies(
        [
            _member("provider", True, [_removed("POST", "/v1/test"), _added("POST", "/v2/test")]),
            _member("consumer", True, [
                _consumption("consumption_removed", "provider", "POST", "/v1/test", "EV-1"),
                _consumption("consumption_added", "provider", "POST", "/v2/test", "EV-2"),
            ]),
        ],
        [_edge("consumer", "provider", "POST /v1/test")],
    )
    assert any(item["classification"] == "coordinated_change" and item["contract_id"] == "POST /v1/test" for item in result["items"])
    assert not any(item["classification"] == "broken" for item in result["items"])


def test_one_consumer_left_behind() -> None:
    result = analyze_interdependencies(
        [
            _member("provider", True, [_removed("POST", "/v1/test"), _added("POST", "/v2/test")]),
            _member("consumer-a", True, [
                _consumption("consumption_removed", "provider", "POST", "/v1/test", "EV-1"),
                _consumption("consumption_added", "provider", "POST", "/v2/test", "EV-2"),
            ]),
            _member("consumer-b"),
        ],
        [
            _edge("consumer-a", "provider", "POST /v1/test"),
            _edge("consumer-b", "provider", "POST /v1/test"),
        ],
    )
    assert _by(result, "coordinated_change", "consumer-a")
    left = _by(result, "broken", "consumer-b")
    assert left["source_service"] == "provider"
    assert "consumer-b" in result["unchanged_but_affected_services"]
    text = fold_assessment({"code": "clear", "label": "CLEAR", "summary": ""}, result, "completed")["summary"]
    assert "consumer-b" in text


def test_new_relationship_is_introduced() -> None:
    result = analyze_interdependencies(
        [
            _member("service-b", True, [_added("GET", "/v1/report")]),
            _member("service-a", True, [_consumption("consumption_added", "service-b", "GET", "/v1/report", "EV-9")]),
        ],
        [],
    )
    item = _by(result, "introduced")
    assert item["source_service"] == "service-b"
    assert item["target_service"] == "service-a"
    assert item["before"]["exists"] is False
    assert item["after"]["exists"] is True


def test_temporal_association_is_not_a_break() -> None:
    result = analyze_interdependencies(
        [_member("provider", True, [_removed("POST", "/v1/test", evidence="EV-1")])],
        [_edge("consumer", "provider", "POST /v1/test", provenance="temporal")],
    )
    assert not any(item["classification"] == "broken" for item in result["items"])
    assert result["temporal_context"]
    assert "not a verified dependency" in result["temporal_context"][0]["statement"].lower()


def test_modified_relationship_is_not_a_break() -> None:
    result = analyze_interdependencies(
        [_member("provider", True, [{"kind": "handler_changed", "method": "POST", "path": "/v1/test", "evidence_ids": ["EV-4"]}])],
        [_edge("consumer", "provider", "POST /v1/test")],
    )
    item = _by(result, "modified", "consumer")
    assert item["classification"] == "modified"
    assert item["required_action"] == ""


def test_weak_evidence_is_potentially_broken() -> None:
    result = analyze_interdependencies(
        [_member("provider", True, [_removed("POST", "/v1/test")])],
        [_edge("consumer", "provider", "POST /v1/test", provenance="unverified")],
    )
    assert _by(result, "potentially_broken")["classification"] == "potentially_broken"


def test_exact_boundary_mode_and_partial_fallback() -> None:
    before = [_edge("consumer", "provider", "POST /v1/test")]
    after = [_edge("consumer", "provider", "POST /v2/test")]
    exact = analyze_interdependencies(
        [_member("provider", True), _member("consumer", True)],
        before_relationships=before,
        after_relationships=after,
    )
    assert exact["comparison_mode"] == "exact_boundary_snapshots"
    assert any(item["classification"] == "coordinated_change" for item in exact["items"])
    partial = analyze_interdependencies([_member("provider", True)], before_relationships=before)
    assert partial["comparison_mode"] == "partial"


def test_dependency_path_uses_only_known_edges() -> None:
    result = analyze_interdependencies(
        [_member("provider", True, [_removed("POST", "/v1/test")])],
        [
            _edge("consumer", "provider", "POST /v1/test"),
            _edge("consumer", "downstream", "GET /v1/next"),
        ],
    )
    path = [step["service_id"] for step in _by(result, "broken")["path"]]
    assert path == ["provider", "consumer", "downstream"]


def test_timeframe_unchanged_consumer_stays_outstanding() -> None:
    result = analyze_interdependencies(
        [
            _member("provider", True, [_removed("POST", "/v1/test"), _added("POST", "/v2/test")], decision="warn", review_id="rev-p"),
            _member("consumer-a", True, [
                _consumption("consumption_removed", "provider", "POST", "/v1/test", "EV-1"),
                _consumption("consumption_added", "provider", "POST", "/v2/test", "EV-2"),
            ], decision="pass", review_id="rev-a"),
            _member("consumer-b", False, decision="", review_id=""),
        ],
        [
            _edge("consumer-a", "provider", "POST /v1/test"),
            _edge("consumer-b", "provider", "POST /v1/test"),
        ],
        source={"kind": "timeframe", "since": "2026-11-01", "until": "2026-11-21"},
    )
    assert result["change_set"]["source_kind"] == "timeframe"
    assert _by(result, "broken", "consumer-b")
    assert "consumer-b" in result["unchanged_but_affected_services"]
    assert not any(row["service_id"] == "consumer-b" and row["changed"] for row in result["change_set"]["repositories"])


def test_change_set_records_refs_without_full_reports() -> None:
    result = analyze_interdependencies(
        [_member("provider", True, [_removed("POST", "/v1/test")], review_id="rev-p")],
        [_edge("consumer", "provider", "POST /v1/test")],
        cluster_id="omnichannel",
        source={"kind": "working_tree"},
    )
    row = result["change_set"]["repositories"][0]
    assert row["service_id"] == "provider"
    assert row["review_id"] == "rev-p"
    assert "ranked_candidates" not in row
    assert result["change_set"]["cluster_id"] == "omnichannel"


def test_report_follows_review_sections_and_exports_agree() -> None:
    analysis = analyze_interdependencies(
        [
            _member("provider", True, [_removed("POST", "/v1/test")], decision="block", review_id="rev-p"),
            _member("consumer"),
        ],
        [_edge("consumer", "provider", "POST /v1/test")],
        cluster_id="omnichannel",
        source={"kind": "timeframe", "since": "2026-11-01", "until": "2026-11-21"},
    )
    record = {
        "cluster_review_id": "cluster-1",
        "cluster_id": "omnichannel",
        "cluster_label": "Omnichannel",
        "source": {"kind": "timeframe", "since": "2026-11-01", "until": "2026-11-21"},
        "counters": {"repositories_inspected": 2, "repositories_with_changes": 1, "repositories_no_change": 1},
        "assessment": fold_assessment({"code": "clear", "label": "CLEAR", "summary": "1 pass"}, analysis, "completed"),
        "interdependencies": analysis,
        "repositories": [
            {"service_id": "provider", "status": "completed", "decision": "block", "review_id": "rev-p", "validation_status": "validated", "commit_count": 2, "change_detected": True},
            {"service_id": "consumer", "status": "no_change", "decision": "", "review_id": "", "commit_count": 0, "change_detected": False},
        ],
    }
    markdown = render_markdown(record)
    assert markdown.startswith("# Nami Cluster Change Review")
    for heading in (
        "## Change summary",
        "## What needs attention",
        "## Interdependency changes",
        "## Cross-repository impact",
        "## Blocking findings",
        "## Warnings",
        "## Review targets",
        "## Repository outcomes",
        "## Verification",
        "## Assessment",
        "## Actions",
        "## Evidence",
        "## Validation / limitations",
    ):
        assert heading in markdown
    assert "not a Boundary Gate decision" in markdown
    assert "POST /v1/test" in markdown
    assert "consumer still uses it" in markdown.lower() or "still uses it" in markdown
    payload = render_json(record)
    pdf = render_pdf(record)
    package = evidence_zip(record)
    assert payload["summary"]["broken"] == analysis["summary"]["broken"] == 1
    assert b"Nami Cluster Change Review" in pdf
    assert b"ATTENTION REQUIRED" in pdf
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        stored = json.loads(archive.read("cluster-review.json"))
        assert stored["summary"]["broken"] == 1
        assert stored["comparison_mode"] == "evidence_derived"
        assert "cluster-review.md" in archive.namelist()
    summary = copy_summary(record)
    assert "ATTENTION REQUIRED" in summary
    assert "1 broken" in summary
    assert "consumer" in summary


def test_cluster_run_keeps_child_decision_and_reports_progress(tmp_path: Path) -> None:
    events: list[dict] = []

    def review(member: dict, _body: dict) -> dict:
        return {
            "review_id": "rev-provider",
            "gate": {"decision": "block", "reason_codes": []},
            "gate_validation": {"status": "validated"},
            "review_scope": {"affected_member_services": ["consumer"]},
            "contract_changes": [_removed("POST", "/v1/test", consumers=[{"service_id": "consumer"}])],
        }

    record = new_cluster_review(
        cluster_id="demo",
        cluster_label="Demo",
        source={"kind": "working_tree"},
        members=[
            {"service_id": "provider", "repository": "provider"},
            {"service_id": "consumer", "repository": "consumer"},
        ],
    )
    output = tmp_path / "output"
    output.mkdir()
    result = execute_cluster_review(
        record,
        settings=Settings(root=tmp_path, repos_file=tmp_path / "repos.txt", output_dir=output, github_token="", ghe_token=""),
        body={"source": {"kind": "working_tree"}, "mode": "fast", "verify": False, "review_scope": "service_cluster", "cluster_id": "demo"},
        publish=lambda _name, data: events.append(data),
        detect=lambda member, _source: {"ok": True, "change_detected": member["service_id"] == "provider", "changed_file_count": 1, "error_code": "", "error_summary": ""},
        review=review,
    )
    provider = next(item for item in result["repositories"] if item["service_id"] == "provider")
    consumer = next(item for item in result["repositories"] if item["service_id"] == "consumer")
    assert provider["decision"] == "block"
    assert consumer["status"] == "no_change"
    assert consumer["decision"] == ""
    broken = _by(result["interdependencies"], "broken", "consumer")
    assert broken["source_service"] == "provider"
    assert result["assessment"]["label"] == "ATTENTION REQUIRED"
    assert any(item.get("analysis_label") == "Comparing interdependencies" for item in events)
    assert "repository_path" not in result
