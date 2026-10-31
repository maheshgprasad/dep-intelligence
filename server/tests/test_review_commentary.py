"""Commentary explains a stored decision and cannot change it."""

from __future__ import annotations

from dep_intel.gate_evaluation import run_scenario
from dep_intel.gate_validation_corpus import scenarios
from dep_intel.review_commentary import attach_commentary, build_commentary, commentary_input, fallback_commentary, validate_commentary


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
