"""REST and MCP call the same review engine."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from dep_intel.api import app
from dep_intel.mcp_server import review_change


class _Engine:
    def __init__(self, settings, cluster=None, publish=None, provider=None) -> None:
        self.body = None

    def run(self, body, cancel=None):
        self.body = body
        return {
            "review_id": "review-1",
            "gate": {"decision": "warn", "reason_codes": ["review_target"], "blocking_findings": [], "warning_findings": [], "review_targets": ["f1"]},
            "findings": [{"finding_id": "f1", "fingerprint": "fp", "title": "Review src/users.js", "category": "review_target"}],
            "summary": {"text": "WARN"},
            "impact": {"affected_services": ["user-service"]},
            "verification": [],
            "score_note": "Ranking scores are not probabilities.",
            "limitations": [],
            "change": {"repository": body["source"]["repository"]},
        }


def test_rest_and_mcp_return_the_same_decision(monkeypatch) -> None:
    monkeypatch.setattr("dep_intel.review_engine.ReviewEngine", _Engine)
    client = TestClient(app)
    response = client.post("/api/review/run", json={"source": {"kind": "working_tree", "repository": "fixtures/auth-service"}, "mode": "fast", "verify": False, "model": {"enabled": False}})
    assert response.status_code == 200
    body = response.json()
    mcp = json.loads(review_change(repository="fixtures/auth-service", source_kind="working_tree", verify=False, model_enabled=False))
    assert body["gate"]["decision"] == mcp["decision"] == "warn"
    assert body["impact"]["affected_services"] == mcp["affected_repositories"]
