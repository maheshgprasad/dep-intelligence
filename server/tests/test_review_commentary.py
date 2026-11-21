"""Commentary explains a stored decision and cannot change it."""

from __future__ import annotations

import copy
import json

from dep_intel.gate_evaluation import run_scenario
from dep_intel.gate_validation_corpus import scenarios
from dep_intel.review_commentary import (
    assess_claims,
    attach_commentary,
    baseline_input,
    build_commentary,
    commentary_input,
    compare_explanations,
    fallback_commentary,
    validate_commentary,
)
from dep_intel.review_model_provider import NoneProvider


def test_temporal_warning_names_the_file_and_does_not_overclaim() -> None:
    report = {
        "gate": {
            "decision": "warn",
            "decision_rules": [{"rule_id": "BG-TEMPORAL-001", "title": "Historical association is not a blocker", "decision": "warn", "outcome": "triggered"}],
        },
        "gate_validation": {"status": "validated", "observed_decision": "warn", "expected_decision": "warn", "summary": "Decision logic validated."},
        "evidence_catalog": [{"evidence_id": "EV-1", "statement": "Historical association.", "source": "git_history", "file_path": "app/main.py", "service_id": "user-service"}],
        "impact": {"affected_services": []},
        "inferences": [],
        "actions": [],
        "limitations": [],
        "change": {"repository": "demo", "service_id": "user-service"},
        "decision_presentation": {},
        "assessment": {},
    }
    commentary = build_commentary(report, provider=_Provider({"headline": "ignore the model"}))
    blob = " ".join(str(commentary.get(field) or "") for field in ("summary", "why_it_matters", "next_action", "evidence_comment")).lower()
    assert "historical association" in blob
    assert "not a blocking condition" in blob
    assert "this evidence alone does not confirm a current structural dependency" in blob
    assert "app/main.py" in blob
    assert "probability" not in blob
    assert "will break" not in blob
    assert "validated nami evidence" not in blob


def test_missing_model_uses_the_contract_fallback() -> None:
    report = _ready(scenarios()[0])
    commentary = build_commentary(report, provider=None)
    assert commentary["mode"] == "deterministic_fallback"
    assert commentary["source"] == "deterministic"
    assert commentary["advisory"] is True
    assert "user-service" in commentary["summary"]
    assert "POST /api/auth/validate" in commentary["summary"]
    assert "contract" in commentary["headline"].lower()
    assert report["gate"]["decision"] == "block"


def test_invalid_json_and_unavailable_model_use_fallback() -> None:
    report = _ready(scenarios()[0])
    broken = build_commentary(report, provider=_Provider("not-json"))
    unavailable = build_commentary(report, provider=_Provider(None, available=False))
    assert broken["mode"] == "deterministic_fallback"
    assert unavailable["mode"] == "deterministic_fallback"
    assert report["gate"]["decision"] == "block"


def test_unknown_service_rule_and_probability_language_are_rejected() -> None:
    report = _ready(scenarios()[0])
    payload = commentary_input(report)
    validation = report["gate_validation"]
    base = fallback_commentary(payload)
    unknown_service = {**base, "summary": "payment-service will fail."}
    unknown_rule = {**base, "evidence_comment": "Rule BG-FAKE-999 fired."}
    probable = {**base, "why_it_matters": "There is an 83% chance of an outage."}
    assert validate_commentary(unknown_service, payload, validation)[0] is False
    assert validate_commentary(unknown_rule, payload, validation)[0] is False
    assert validate_commentary(probable, payload, validation)[0] is False
    rejected = build_commentary(report, provider=_Provider(unknown_service))
    assert rejected["mode"] == "deterministic_fallback"
    assert "payment-service" not in rejected["summary"]


def test_a_decision_field_is_rejected_and_the_gate_is_unchanged() -> None:
    report = _ready(scenarios()[0])
    malicious = {
        "decision": "pass",
        "headline": "Safe to deploy",
        "summary": "This change is safe to proceed.",
        "why_it_matters": "Nothing to do.",
        "next_action": "Deploy it.",
        "evidence_comment": "None.",
        "validation_comment": "Fully validated.",
        "limitation_note": "",
    }
    commentary = build_commentary(report, provider=_Provider(malicious))
    stored = attach_commentary(report, provider=_Provider(malicious))
    assert commentary["mode"] == "deterministic_fallback"
    assert "decision" not in commentary
    assert stored["gate"]["decision"] == "block"
    assert stored["findings"][0]["evidence_strength"] == report["findings"][0]["evidence_strength"]


def test_concise_commentary_is_accepted_and_overlong_text_is_not() -> None:
    report = _ready(next(item for item in scenarios() if item["name"] == "clean-safe-change"))
    payload = commentary_input(report)
    text = fallback_commentary(payload)
    assert validate_commentary(text, payload, report["gate_validation"])[0] is True
    long_text = {**text, "summary": " ".join(["word"] * 80) + "."}
    assert validate_commentary(long_text, payload, report["gate_validation"])[1] == "too_long"
    attached = attach_commentary(report)
    for field in ("headline", "summary", "why_it_matters", "next_action", "evidence_comment", "validation_comment"):
        assert attached["commentary"][field]


def test_validation_failure_commentary_does_not_reassure() -> None:
    report = _ready(scenarios()[0])
    report["gate_validation"] = {**report["gate_validation"], "status": "failed", "decision_match": False}
    commentary = build_commentary(report, provider=_Provider({
        "headline": "Safe to deploy",
        "summary": "Fully validated and safe to proceed.",
        "why_it_matters": "All checks passed.",
        "next_action": "Ship it.",
        "evidence_comment": "None.",
        "validation_comment": "Fully validated.",
        "limitation_note": "",
    }))
    assert commentary["headline"] == "Gate decision requires technical review"
    blob = " ".join(commentary[field] for field in ("headline", "summary", "next_action", "validation_comment")).lower()
    assert "could not" in blob or "not confirmed" in blob
    assert "safe to proceed" not in blob
    assert report["gate"]["decision"] == "block"


def test_valid_evidence_claim_is_accepted_and_the_gate_stays() -> None:
    report = _ready(scenarios()[0])
    before = copy.deepcopy(report["gate"])
    evidence_id = report["evidence_catalog"][0]["evidence_id"]
    prose = _prose(report)
    prose["claims"] = [{
        "claim": "user-service consumes POST /api/auth/validate from auth-service.",
        "evidence_ids": [evidence_id],
    }]
    commentary = build_commentary(report, provider=_Provider(prose))
    stored = attach_commentary(report, provider=_Provider(prose))
    assert commentary["mode"] == "llm"
    assert commentary["claims"] == [{"claim": prose["claims"][0]["claim"], "evidence_ids": [evidence_id]}]
    assert commentary["claim_verification"]["accepted"] == 1
    assert commentary["claim_verification"]["generated"] == 1
    assert "accepted against stored" in commentary["claims_note"]
    assert "proven" not in commentary["claims_note"]
    assert stored["gate"] == before


def test_unsupported_claims_are_rejected_without_changing_the_gate() -> None:
    report = _ready(scenarios()[0])
    before = copy.deepcopy(report["gate"])
    evidence_id = report["evidence_catalog"][0]["evidence_id"]
    prose = _prose(report)
    cases = {
        "unknown_evidence": {"claim": "auth-service removed the operation.", "evidence_ids": ["EV-999"]},
        "unknown_identity": {"claim": "fake-service consumes the operation.", "evidence_ids": [evidence_id]},
        "forbidden_language": {"claim": "auth-service is safe to deploy.", "evidence_ids": [evidence_id]},
    }
    for reason, claim in cases.items():
        checked = assess_claims([claim], report)
        assert checked["claim_verification"]["accepted"] == 0
        assert checked["claim_verification"]["rejections"] == [{"reason": reason}]
        assert claim["claim"] not in json.dumps(checked["claims"])
    warn = _ready(next(item for item in scenarios() if item["name"] == "body-only-handler-change"))
    warn_id = warn["evidence_catalog"][0]["evidence_id"]
    conflict = assess_claims([{"claim": "The change is BLOCKED.", "evidence_ids": [warn_id]}], warn)
    assert conflict["claim_verification"]["rejections"] == [{"reason": "decision_conflict"}]
    prose["claims"] = [
        cases["unknown_evidence"],
        cases["unknown_identity"],
        {"claim": "The change is PASS.", "evidence_ids": [evidence_id]},
        cases["forbidden_language"],
    ]
    commentary = build_commentary(report, provider=_Provider(prose))
    stored = attach_commentary(report, provider=_Provider(prose))
    assert commentary["claims"] == []
    assert commentary["claim_verification"]["generated"] == 4
    assert commentary["claim_verification"]["rejected"] == 4
    assert "EV-999" not in json.dumps(commentary["claims"])
    assert "fake-service" not in json.dumps(commentary["claims"])
    assert stored["gate"] == before


def test_missing_model_keeps_the_deterministic_explanation() -> None:
    report = _ready(scenarios()[0])
    before = copy.deepcopy(report["gate"])
    commentary = build_commentary(report, provider=NoneProvider())
    stored = attach_commentary(report, provider=NoneProvider())
    assert commentary["mode"] == "deterministic_fallback"
    assert "claims" not in commentary
    assert "claims_note" not in commentary
    assert commentary["summary"]
    assert stored["gate"] == before


def test_grounded_comparison_rejects_baseline_claims_and_keeps_the_gate() -> None:
    report = _ready(scenarios()[0])
    before = copy.deepcopy(report["gate"])
    evidence_id = report["evidence_catalog"][0]["evidence_id"]
    provider = _SplitProvider(evidence_id)
    result = compare_explanations(report, provider)
    assert provider.baseline_request is not None
    assert "EV-" not in json.dumps(provider.baseline_request)
    assert "evidence_id" not in json.dumps(provider.baseline_request)
    assert result["baseline"]["invalid_evidence_reference_count"] == 1
    assert result["baseline"]["unknown_identity_count"] == 0 or result["baseline"]["rejected_claims"] == 1
    assert result["baseline"]["accepted_evidence_linked_claims"] == 0
    assert result["grounded"]["accepted_evidence_linked_claims"] == 1
    assert result["gate_decision"] == "block"
    assert report["gate"] == before


def test_templates_cover_pass_warn_block_and_incomplete() -> None:
    names = {
        "clean-safe-change": "pass",
        "body-only-handler-change": "warn",
        "consumed-contract-removal": "block",
        "stale-graph-strict": "incomplete",
    }
    for name, decision in names.items():
        report = _ready(next(item for item in scenarios() if item["name"] == name))
        commentary = build_commentary(report)
        assert report["gate"]["decision"] == decision
        assert commentary["headline"]
        assert commentary["next_action"]
        assert validate_commentary(
            {field: commentary[field] for field in ("headline", "summary", "why_it_matters", "next_action", "evidence_comment", "validation_comment", "limitation_note")},
            commentary_input(report),
            report["gate_validation"],
        )[0] is True


def _prose(report: dict) -> dict:
    text = fallback_commentary(commentary_input(report))
    return {field: text[field] for field in ("headline", "summary", "why_it_matters", "next_action", "evidence_comment", "validation_comment", "limitation_note")}


class _SplitProvider:
    name = "fake"
    model = "test-model"

    def __init__(self, evidence_id: str) -> None:
        self.evidence_id = evidence_id
        self.baseline_request = None

    def complete(self, purpose: str, request: dict, timeout: float = 20.0) -> dict:
        if purpose == "explanation_baseline":
            self.baseline_request = request
            return {"available": True, "provider": self.name, "parsed": {"claims": [{"claim": "fake-service will fail.", "evidence_ids": ["EV-999"]}]}}
        return {
            "available": True,
            "provider": self.name,
            "parsed": {"claims": [{"claim": "user-service consumes POST /api/auth/validate from auth-service.", "evidence_ids": [self.evidence_id]}]},
        }


class _Provider:
    name = "fake"
    model = "test-model"

    def __init__(self, parsed, available: bool = True) -> None:
        self.parsed = parsed
        self.available = available

    def complete(self, purpose: str, request: dict, timeout: float = 20.0) -> dict:
        assert purpose == "commentary"
        assert timeout > 0
        if isinstance(self.parsed, str):
            return {"available": True, "provider": self.name, "model": self.model, "parsed": self.parsed, "purpose": purpose}
        return {"available": self.available, "provider": self.name, "model": self.model, "parsed": self.parsed, "purpose": purpose, "diagnostic": "" if self.available else "model_unavailable"}


def _ready(scenario: dict) -> dict:
    report = run_scenario(scenario)
    report.pop("commentary", None)
    return report
