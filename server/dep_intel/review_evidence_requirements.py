"""Required evidence for claims that can affect a Boundary Gate decision.

The registry is data. It does not execute policy code.
"""

from __future__ import annotations

from typing import Any

CLAIM_REQUIREMENTS: dict[str, dict[str, Any]] = {
    "consumed_contract_break": {
        "required": ["contract_removed", "known_consumer"],
        "requires_current_structural_evidence": True,
        "blocker": True,
        "rule_id": "BG-CONTRACT-001",
    },
    "required_verification_failure": {
        "required": ["verification_executed", "verification_failed"],
        "requires_current_structural_evidence": False,
        "blocker": True,
        "rule_id": "BG-VERIFY-001",
    },
    "forbidden_pattern_violation": {
        "required": ["changed_added_line", "policy_match"],
        "requires_current_structural_evidence": False,
        "blocker": True,
        "rule_id": "BG-POLICY-001",
    },
    "verified_critical": {
        "required": ["policy_match"],
        "requires_current_structural_evidence": False,
        "blocker": True,
        "rule_id": "BG-CRITICAL-001",
    },
    "historical_association": {
        "required": ["historical_association"],
        "requires_current_structural_evidence": False,
        "blocker": False,
        "max_grade": "weak",
        "rule_id": "BG-TEMPORAL-001",
    },
    "llm_advisory": {
        "required": [],
        "requires_current_structural_evidence": False,
        "blocker": False,
        "max_grade": "weak",
        "rule_id": "BG-AI-001",
    },
    "test_gap": {
        "required": [],
        "requires_current_structural_evidence": False,
        "blocker": False,
        "rule_id": "",
    },
}

DECISIVE_COUNTERS = {
    "consumed_contract_break": {"openapi_operation_present", "contract_still_present"},
    "forbidden_pattern_violation": {"removed_block_only"},
    "test_gap": {"tested_by"},
}
