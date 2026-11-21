"""Human-readable Boundary Gate evidence is stored once and reused by every export."""

from __future__ import annotations

import inspect
import io
import json
import zipfile

from dep_intel.gate_evaluation import run_scenario
from dep_intel.gate_validation_corpus import scenarios
from dep_intel.review_commentary import attach_commentary, fallback_commentary, commentary_input
from dep_intel.review_explain import attach_gate_evidence_summary, build_gate_evidence_summary
from dep_intel.review_export import build_zip, canonical_export, pdf_lines
from dep_intel.review_pdf import render_text_pdf
from dep_intel.review_store import render_markdown


def test_summary_does_not_decide_the_gate() -> None:
    source = inspect.getsource(build_gate_evidence_summary) + inspect.getsource(attach_gate_evidence_summary)
    assert "decide_gate(" not in source


def test_block_summary_names_the_contract_consumer_action_and_validation() -> None:
    report = _scenario("consumed-contract-removal")
    assert report["gate"]["decision"] == "block"
    summary = report["gate_evidence_summary"]
    blob = json.dumps(summary).lower()
    assert "removed contract" in blob
    assert "known consumer" in blob
    assert "required action" in blob
    assert "bg-contract-001" in blob
    assert "ev-" in blob
    assert summary["validation"]["status"] == "validated"
    assert "user-service" in summary["summary"]
    assert "post /api/auth/validate" in summary["summary"].lower()
    assert "safe" not in blob


def test_temporal_warning_stays_a_historical_association() -> None:
    report = _scenario("temporal-only-target")
    assert report["gate"]["decision"] == "warn"
    blob = json.dumps(report["gate_evidence_summary"]).lower()
    assert "historical association" in blob
    assert "not a current dependency" in blob
    assert "non-blocking" in blob
    assert "confirmed current dependency" not in blob


def test_incomplete_names_the_missing_evidence() -> None:
    report = _scenario("stale-graph-strict")
    assert report["gate"]["decision"] == "incomplete"
    summary = report["gate_evidence_summary"]["summary"].lower()
    assert "missing required evidence" in summary
    assert "graph" in summary
    assert "pass, warn, or block" in summary


def test_pass_reports_no_verified_blocker() -> None:
    report = _scenario("clean-safe-change")
    assert report["gate"]["decision"] == "pass"
    blob = json.dumps(report["gate_evidence_summary"]).lower()
    assert "no verified blocker" in blob
    assert "safe" not in blob


def test_the_same_gate_explanation_is_in_json_markdown_and_pdf() -> None:
    report = _scenario("consumed-contract-removal")
    summary = report["gate_evidence_summary"]
    sentence = summary["summary"]
    evidence_id = summary["items"][0]["evidence"][0]["evidence_id"]
    statement = summary["items"][0]["evidence"][0]["statement"]
    payload = canonical_export(report, {"config_hash": "abc"}, {"items": []})
    assert payload["gate_evidence_summary"]["summary"] == sentence
    assert payload["gate_decision"] == "block"
    markdown = render_markdown(report)
    pdf_text = "\n".join(pdf_lines(payload))
    for surface in (json.dumps(payload), markdown, pdf_text):
        assert sentence in surface
        assert "user-service" in surface
        assert "POST /api/auth/validate" in surface
        assert "BG-CONTRACT-001" in surface
        assert evidence_id in surface
        assert statement in surface
        assert "Boundary Gate evidence" in surface
    pdf = render_text_pdf(pdf_lines(payload))
    assert b"Boundary Gate evidence" in pdf
    assert b"BG-CONTRACT-001" in pdf
    assert b"user-service" in pdf
    package = build_zip(report["review_id"], payload, report)
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        names = archive.namelist()
        summary_name = next(name for name in names if name.endswith("/gate/gate-evidence-summary.json"))
        stored = json.loads(archive.read(summary_name))
        for suffix in (
            "/gate/decision.json",
            "/gate/rules.json",
            "/gate/actions.json",
            "/evidence/evidence.json",
            "/validation/gate-validation.json",
        ):
            assert any(name.endswith(suffix) for name in names)
    assert stored["summary"] == sentence
    assert stored["items"][0]["rule_id"] == "BG-CONTRACT-001"


def test_accepted_ai_claims_use_the_stored_wording_in_exports() -> None:
    report = _scenario("consumed-contract-removal")
    evidence_id = report["gate_evidence_summary"]["items"][0]["evidence"][0]["evidence_id"]
    claim = "user-service consumes POST /api/auth/validate from auth-service."
    prose = fallback_commentary(commentary_input(report))
    prose["claims"] = [{"claim": claim, "evidence_ids": [evidence_id]}]
    report = attach_commentary({**report, "commentary": {}}, provider=_Provider(prose))
    report.pop("commentary", None)
    report = attach_commentary(report, provider=_Provider(prose))
    before = report["gate"]["decision"]
    payload = canonical_export(report, {}, {})
    assert payload["commentary"]["claims"][0]["claim"] == claim
    assert payload["commentary"]["claims"][0]["evidence_ids"] == [evidence_id]
    assert payload["gate_decision"] == before == "block"
    note = payload["commentary"]["claims_note"]
    markdown = render_markdown(report)
    pdf_text = "\n".join(pdf_lines(payload))
    for surface in (markdown, pdf_text):
        assert claim in surface
        assert evidence_id in surface
        assert note in surface
        assert "advisory and did not influence" in surface
    assert "EV-999" not in pdf_text


class _Provider:
    name = "fake"
    model = "test-model"

    def __init__(self, parsed: dict) -> None:
        self.parsed = parsed

    def complete(self, purpose: str, request: dict, timeout: float = 20.0) -> dict:
        return {"available": True, "provider": self.name, "model": self.model, "parsed": self.parsed, "purpose": purpose}


def _scenario(name: str) -> dict:
    return run_scenario(next(item for item in scenarios() if item["name"] == name))
