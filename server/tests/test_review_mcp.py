"""MCP review tools use the shared engine and reject unknown feedback."""

from __future__ import annotations

import json

from dep_intel.mcp_server import explain_review_finding, review_change
from dep_intel.review_engine import ReviewEngine
from dep_intel.review_feedback import record_feedback


def test_mcp_review_change_uses_the_engine(monkeypatch) -> None:
    class Engine:
        def __init__(self, settings, cluster=None, publish=None, provider=None) -> None:
            self.settings = settings

        def run(self, body, cancel=None):
            return {
                "review_id": "mcp-1",
                "gate": {"decision": "pass", "reason_codes": [], "blocking_findings": [], "warning_findings": [], "review_targets": []},
                "findings": [],
                "summary": {"text": "PASS"},
                "impact": {"affected_services": []},
                "verification": [{"id": "none", "status": "skipped"}],
                "score_note": "Ranking scores are not probabilities.",
                "limitations": [],
                "change": {"repository": body["source"]["repository"]},
            }

    monkeypatch.setattr("dep_intel.review_engine.ReviewEngine", Engine)
    payload = json.loads(review_change(repository="fixtures/auth-service", verify=False, model_enabled=False))
    assert payload["decision"] == "pass"
    assert "report_path" in payload


def test_explain_missing_finding(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(ReviewEngine, "explain", lambda self, review_id, finding_id: {"code": "review_not_found"})
    payload = json.loads(explain_review_finding("missing", "finding"))
    assert payload["code"] == "review_not_found"
    status, body = record_feedback(tmp_path, "missing", {"finding_fingerprint": "nope", "status": "false_positive"})
    assert status == 404
    assert body["code"] == "review_not_found"
