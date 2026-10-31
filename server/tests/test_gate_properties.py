"""Metamorphic checks. Removing evidence must not make a Gate decision more severe."""

from __future__ import annotations

from copy import deepcopy

from dep_intel.gate_validation_corpus import scenarios
from dep_intel.review_gate import decide_gate
from dep_intel.review_gate_validation import replay_gate_decision

_RANK = {"pass": 0, "warn": 1, "incomplete": 2, "block": 3}


def _decision(scenario: dict) -> str:
    gate = decide_gate(
        scenario["findings"],
        scenario["verification"],
        scenario["config"],
        alignment=scenario["change"]["graph_alignment"],
        diagnostics=scenario["diagnostics"],
    )
    return gate["decision"]


def test_removing_known_consumer_does_not_increase_severity() -> None:
    original = next(item for item in scenarios() if item["name"] == "consumed-contract-removal")
    weakened = deepcopy(original)
    finding = weakened["findings"][0]
    finding["structured"]["contract"]["consumers"] = []
    finding["evidence"][0]["structured_data"]["consumers"] = []
    finding["category"] = "review_target"
    finding["evidence_strength"] = "moderate"
    assert _RANK[_decision(weakened)] <= _RANK[_decision(original)]


def test_priority_score_does_not_change_the_gate() -> None:
    scenario = deepcopy(next(item for item in scenarios() if item["name"] == "consumed-contract-removal"))
    before = _decision(scenario)
    scenario["findings"][0]["priority_score"] = 0
    after_low = _decision(scenario)
    scenario["findings"][0]["priority_score"] = 999
    after_high = _decision(scenario)
    assert before == after_low == after_high


def test_replay_ignores_priority_score() -> None:
    report = {
        "gate": {"decision": "block", "reason_codes": ["contract_break"], "blocking_findings": ["contract-1"]},
        "findings": deepcopy(next(item for item in scenarios() if item["name"] == "consumed-contract-removal")["findings"]),
        "verification": [],
        "diagnostics": [],
        "change": {"graph_alignment": "exact"},
        "review_control": {},
    }
    settings = {"strict_graph": False, "incomplete_on_required_verification_failure": True, "block_on_verified_critical": True, "block_on_verified_high_blocker_policy": True, "block_on_breaking_contract_with_known_consumers": True}
    report["findings"][0]["priority_score"] = 0.1
    low = replay_gate_decision(report, settings)
    report["findings"][0]["priority_score"] = 100
    high = replay_gate_decision(report, settings)
    assert low == high
