"""Independent Gate validation. The validator does not call the gate implementation."""

from __future__ import annotations

import copy
import io
import zipfile
from pathlib import Path

from dep_intel.gate_validation_corpus import scenarios
from dep_intel.review_explain import attach_explanation
from dep_intel.review_export import build_zip, canonical_export, pdf_lines
from dep_intel.review_gate import decide_gate
from dep_intel.review_commentary import attach_commentary
from dep_intel.review_gate_validation import attach_gate_validation, gate_input_hash, validate_gate
from dep_intel.review_pdf import render_text_pdf
from dep_intel.review_store import render_markdown

ROOT = Path(__file__).resolve().parents[2]
SOURCE = (ROOT / "server/dep_intel/review_gate_validation.py").read_text(encoding="utf-8")


def test_validator_does_not_call_the_gate_implementation() -> None:
    assert "decide_gate" not in SOURCE


def test_consumed_contract_is_independently_validated() -> None:
    report = _explain(scenarios()[0])
    validation = report["gate_validation"]
    assert report["gate"]["decision"] == "block"
    assert validation["status"] == "validated"
    assert validation["decision_match"] is True
    assert validation["observed_decision"] == validation["expected_decision"] == "block"
    rule = next(item for item in validation["rule_validation"] if item["rule_id"] == "BG-CONTRACT-001")
    assert rule["status"] == "validated"
    assert {item["requirement"] for item in rule["requirements"] if item["status"] == "satisfied"} >= {"contract_removed", "known_consumer", "current_structural_evidence"}
    assert rule["counter_evidence_status"] == "none_decisive"
    assert validation["invariants"]["failed"] == 0
    assert validation["invariants"]["total"] == 10
    assert validation["counterfactuals"]["failed"] == 0
    assert validation["counterfactuals"]["total"] >= 2
    assert validation["reproducibility"]["gate_input_hash"].startswith("sha256:")
    assert "not digital signature" in validation["reproducibility"]["note"].lower() or "not a digital signature" in validation["reproducibility"]["note"].lower()


def test_missing_consumer_fails_the_contract_proof_without_changing_the_decision() -> None:
    scenario = copy.deepcopy(scenarios()[0])
    finding = scenario["findings"][0]
    finding["structured"] = {"contract": {"consumers": []}}
    finding["evidence"][0]["structured_data"]["consumers"] = []
    scenario["contract_changes"] = []
    report = _explain(scenario)
    assert report["gate"]["decision"] == "block"
    validation = validate_gate(report, scenario["config"])
    rule = next(item for item in validation["rule_validation"] if item["rule_id"] == "BG-CONTRACT-001")
    assert rule["status"] == "failed"
    assert validation["status"] == "failed"
    assert report["gate"]["decision"] == "block"


def test_counter_evidence_invalidates_a_blocking_proof() -> None:
    scenario = copy.deepcopy(scenarios()[0])
    scenario["findings"][0]["counter_evidence"] = [{
        "kind": "openapi_operation_present",
        "source": "OpenAPI",
        "contradicts": ["consumed_contract_break"],
        "statement": "HEAD OpenAPI still defines the operation.",
    }]
    report = _explain(scenario)
    assert report["gate"]["decision"] == "block"
    rule = next(item for item in report["gate_validation"]["rule_validation"] if item["rule_id"] == "BG-CONTRACT-001")
    assert rule["counter_evidence_status"] == "decisive_contradiction"
    assert rule["status"] == "failed"
    assert report["gate_validation"]["status"] == "failed"
    assert report["gate"]["decision"] == "block"


def test_temporal_llm_and_rank_only_blockers_fail_invariants() -> None:
    temporal = _forced_block({
        "finding_id": "temporal",
        "category": "review_target",
        "severity": "info",
        "evidence_strength": "verified",
        "source": "NRM",
        "blocking": True,
        "structured": {"evidence_origin": "temporal_only", "hybrid_score": 0.99},
        "evidence": [{"source": "NRM", "summary": "history"}],
    })
    assert temporal["gate_validation"]["status"] == "failed"
    assert temporal["gate"]["decision"] == "block"
    assert any(item["invariant_id"] == "INV-002" and item["status"] == "failed" for item in temporal["gate_validation"]["invariants"]["items"])
    model = _forced_block({
        "finding_id": "model",
        "category": "regression_risk",
        "severity": "critical",
        "evidence_strength": "verified",
        "source": "LLM",
        "blocking": True,
        "structured": {},
        "evidence": [{"source": "LLM", "summary": "maybe"}],
    })
    assert model["gate_validation"]["status"] == "failed"
    assert any(item["invariant_id"] == "INV-003" and item["status"] == "failed" for item in model["gate_validation"]["invariants"]["items"])
    rank = _forced_block({
        "finding_id": "rank",
        "category": "cross_repo_impact",
        "severity": "critical",
        "evidence_strength": "verified",
        "source": "NRM",
        "blocking": True,
        "service_id": "user-service",
        "file_path": "src/users.js",
        "structured": {"evidence_origin": "structural_only", "hybrid_score": 0.99},
        "evidence": [{"source": "NRM", "summary": "rank", "structured_data": {"evidence_origin": "structural_only"}}],
    })
    assert rank["gate"]["decision"] == "block"
    assert rank["gate_validation"]["status"] == "failed"
    assert any(item["invariant_id"] == "INV-001" and item["status"] == "failed" for item in rank["gate_validation"]["invariants"]["items"])


def test_counterfactuals_disable_contract_verification_and_grade() -> None:
    contract = _explain(scenarios()[0])["gate_validation"]["counterfactuals"]["items"]
    assert any(item["case_id"] == "CF-CONTRACT-CONSUMER" and item["status"] == "passed" and item["observed"] == "rule_not_satisfied" for item in contract)
    assert any(item["case_id"] == "CF-GRADE" and item["status"] == "passed" for item in contract)
    assert any(item["case_id"] == "CF-FRESHNESS" and item["status"] == "passed" for item in contract)
    verify = next(item for item in scenarios() if item["name"] == "required-verification-failed")
    checks = _explain(verify)["gate_validation"]["counterfactuals"]["items"]
    assert any(item["case_id"] == "CF-VERIFY-PASS" and item["observed"] == "rule_not_satisfied" and item["status"] == "passed" for item in checks)


def test_identical_inputs_share_a_gate_input_hash() -> None:
    report = _explain(scenarios()[0])
    config = scenarios()[0]["config"]
    first = report["gate_validation"]["reproducibility"]["gate_input_hash"]
    second = validate_gate(report, config)["reproducibility"]["gate_input_hash"]
    assert second == first
    changed = copy.deepcopy(report)
    for finding in changed["findings"]:
        finding["priority_score"] = 0
    changed["commentary"] = {"headline": "different"}
    assert gate_input_hash(changed) == first
    assert second == first


def test_decision_mismatch_stays_visible() -> None:
    report = _explain(scenarios()[0])
    report["gate"]["decision"] = "pass"
    report.pop("gate_validation")
    checked = attach_gate_validation(report, scenarios()[0]["config"])
    assert checked["gate"]["decision"] == "pass"
    assert checked["gate_validation"]["status"] == "failed"
    assert checked["gate_validation"]["decision_match"] is False


def test_stale_graph_modes_follow_the_stored_policy() -> None:
    strict = next(item for item in scenarios() if item["name"] == "stale-graph-strict")
    relaxed = next(item for item in scenarios() if item["name"] == "stale-graph-non-strict")
    strict_report = _explain(strict)
    relaxed_report = _explain(relaxed)
    assert strict_report["gate"]["decision"] == "incomplete"
    assert strict_report["gate_validation"]["status"] == "validated"
    assert relaxed_report["gate"]["decision"] == "warn"
    assert relaxed_report["gate_validation"]["expected_decision"] == "warn"


def test_exports_include_validation_and_commentary() -> None:
    report = attach_commentary(_explain(scenarios()[0]))
    payload = canonical_export(report, {"config_hash": "abc"}, {"items": []})
    assert payload["schema_version"] == 1
    assert payload["gate_validation"]["status"] == "validated"
    assert payload["commentary"]["headline"]
    assert payload["gate_decision"] == "block"
    assert payload["reproducibility"]["gate_input_hash"] != payload["reproducibility"]["report_hash"]
    text = "\n".join(pdf_lines(payload))
    for phrase in ("Nami explanation", "Decision logic validation", "Safety checks", "Counterfactual", "Evidence"):
        assert phrase in text
    pdf = render_text_pdf(pdf_lines(payload))
    assert b"Decision logic validation" in pdf
    assert b"Nami explanation" in pdf
    markdown = render_markdown(report)
    assert "## Decision logic validation" in markdown
    assert "## Nami explanation" in markdown
    package = build_zip(report["review_id"], payload, report)
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        names = archive.namelist()
    assert any(name.endswith("/validation/gate-validation.json") for name in names)
    assert any(name.endswith("/validation/invariants.json") for name in names)
    assert any(name.endswith("/validation/counterfactuals.json") for name in names)
    assert any(name.endswith("/commentary/commentary.json") for name in names)


def _explain(scenario: dict) -> dict:
    config = scenario["config"]
    alignment = scenario["change"]["graph_alignment"]
    gate = decide_gate(scenario["findings"], scenario["verification"], config, alignment=alignment, diagnostics=scenario["diagnostics"])
    report = attach_explanation({
        "review_id": scenario["name"],
        "gate": {key: value for key, value in gate.items() if key != "findings"},
        "findings": gate["findings"],
        "change": scenario["change"],
        "verification": scenario["verification"],
        "diagnostics": scenario["diagnostics"],
        "contract_changes": scenario["contract_changes"],
        "policies": [],
        "impact": scenario["impact"],
        "limitations": [],
        "review_control": {"pre_gate_abort": False, "cancelled": False},
    }, config)
    return attach_gate_validation(report, config)


def _forced_block(finding: dict) -> dict:
    report = attach_explanation({
        "review_id": finding["finding_id"],
        "gate": {
            "decision": "block",
            "reason_codes": [finding["category"]],
            "blocking_findings": [finding["finding_id"]],
            "warning_findings": [],
            "review_targets": [],
            "evidence_complete": True,
        },
        "findings": [finding],
        "change": {"repository": "auth-service", "service_id": "auth-service", "base_sha": "basesha", "head_sha": "headsha", "graph_alignment": "exact", "graph_version": "graph-1"},
        "verification": [],
        "diagnostics": [],
        "contract_changes": [],
        "policies": [],
        "impact": {"affected_services": [], "ranked_candidates": []},
        "limitations": [],
    }, {"gate": {}, "enforcement": {"mode": "advisory"}})
    return attach_gate_validation(report, {"gate": {}})
