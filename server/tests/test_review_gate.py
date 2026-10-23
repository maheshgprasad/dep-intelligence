"""Gate decisions stay independent of NRM rank."""

from __future__ import annotations

from dep_intel.review_gate import decide_gate


def test_gate_outcomes() -> None:
    config = {"gate": {"strict_graph": False, "block_on_verified_critical": True, "block_on_verified_high_blocker_policy": True, "block_on_breaking_contract_with_known_consumers": True}, "review": {"max_findings": 20}}
    critical = _finding("security_risk", "critical", "verified", blocking=True)
    assert decide_gate([critical], [], config, alignment="exact", diagnostics=[])["decision"] == "block"
    blocker = _finding("policy_violation", "high", "verified", blocking=True)
    assert decide_gate([blocker], [], config, alignment="exact", diagnostics=[])["decision"] == "block"
    contract = _finding("contract_break", "high", "verified", blocking=True)
    assert decide_gate([contract], [], config, alignment="exact", diagnostics=[])["decision"] == "block"
    warning = _finding("test_gap", "medium", "strong")
    assert decide_gate([warning], [], config, alignment="exact", diagnostics=[])["decision"] == "warn"
    assert decide_gate([], [], config, alignment="exact", diagnostics=[])["decision"] == "pass"
    incomplete = decide_gate([], [{"id": "unit", "required": True, "status": "skipped"}], {**config, "gate": {**config["gate"], "incomplete_on_required_verification_failure": True}}, alignment="exact", diagnostics=[])
    assert incomplete["decision"] == "incomplete"
    ranked = _finding("review_target", "info", "moderate", origin="structural_only", hybrid=0.99)
    assert decide_gate([ranked], [], config, alignment="exact", diagnostics=[])["decision"] != "block"
    temporal = _finding("review_target", "info", "weak", origin="temporal_only", hybrid=0.99)
    temporal_gate = decide_gate([temporal], [], config, alignment="exact", diagnostics=[])
    assert temporal_gate["decision"] != "block"
    assert "temporal_only_review_target" in temporal_gate["reason_codes"]


def _finding(category: str, severity: str, strength: str, *, blocking: bool = False, origin: str = "", hybrid: float | None = None) -> dict:
    return {
        "finding_id": f"{category}-{severity}-{origin}",
        "category": category,
        "severity": severity,
        "title": category,
        "claim": category,
        "evidence_strength": strength,
        "blocking": blocking,
        "source": "NRM" if origin else "policy",
        "file_path": "src/a.js",
        "evidence": [],
        "structured": {"evidence_origin": origin, "hybrid_score": hybrid, "rank": 1},
        "verification_status": "failed" if category == "verification_failure" else "not_requested",
    }
