"""Declarative Boundary Gate rule checks.

The registry describes what would justify a triggered rule. It does not
choose pass, warn, block, or incomplete.
"""

from __future__ import annotations

from typing import Any

VALIDATOR_VERSION = "1.0"

# Deterministic tool observations. manifest and test are current source names
# for declared topology and test records already accepted by the evidence gate.
HARD_EVIDENCE = frozenset({
    "diff",
    "CRG",
    "cluster_graph",
    "OpenAPI",
    "verification_command",
    "policy",
    "generated_topology",
    "deployment_topology",
    "manifest",
    "test",
})

SUPPORTING_EVIDENCE = frozenset({
    "git_history",
    "NRM",
})

ADVISORY_EVIDENCE = frozenset({
    "LLM",
})

SOURCE_CATEGORY = {
    **{name: "hard" for name in HARD_EVIDENCE},
    **{name: "supporting" for name in SUPPORTING_EVIDENCE},
    **{name: "advisory" for name in ADVISORY_EVIDENCE},
}

SOFT_EVIDENCE = SUPPORTING_EVIDENCE | ADVISORY_EVIDENCE

REQUIREMENT_LABELS = {
    "contract_removed": "Contract removal confirmed",
    "known_consumer": "Known consumer confirmed",
    "current_structural_evidence": "Structural evidence current",
    "verification_executed": "Verification command ran",
    "verification_failed": "Verification finished with a non-zero exit",
    "policy_match": "Project rule matched the change",
    "changed_added_line": "The match is on a changed line",
    "historical_association": "Historical association recorded",
    "model_suggestion": "Model suggestion recorded as advisory",
    "structural_path": "A structural path was recorded",
    "graph_not_current": "Current graph evidence is missing",
    "required_evidence_unavailable": "Required evidence did not finish",
}

# Blocking proofs require the listed evidence and a verified grade.
# Non-blocking rows explain warn and incomplete. They are not block proofs.
GATE_RULE_VALIDATION: dict[str, dict[str, Any]] = {
    "BG-CONTRACT-001": {
        "title": "Breaking contract with known consumers",
        "decision": "block",
        "required_claim_type": "consumed_contract_break",
        "required_grade": "verified",
        "required_evidence": ["contract_removed", "known_consumer"],
        "requires_current_structural_evidence": True,
        "decisive_counter_kinds": ["openapi_operation_present", "contract_still_present"],
    },
    "BG-VERIFY-001": {
        "title": "Required verification failed",
        "decision": "block",
        "required_claim_type": "required_verification_failure",
        "required_grade": "verified",
        "required_evidence": ["verification_executed", "verification_failed"],
        "requires_current_structural_evidence": False,
        "decisive_counter_kinds": [],
    },
    "BG-POLICY-001": {
        "title": "Verified blocking policy violation",
        "decision": "block",
        "required_claim_type": "forbidden_pattern_violation",
        "required_grade": "verified",
        "required_evidence": ["changed_added_line", "policy_match"],
        "requires_current_structural_evidence": False,
        "decisive_counter_kinds": ["removed_block_only"],
    },
    "BG-CRITICAL-001": {
        "title": "Verified critical evidence",
        "decision": "block",
        "required_claim_type": "verified_critical",
        "required_grade": "verified",
        "required_evidence": ["policy_match"],
        "requires_current_structural_evidence": False,
        "decisive_counter_kinds": ["removed_block_only"],
    },
    "BG-GRAPH-001": {
        "title": "Required graph evidence unavailable",
        "decision": "incomplete",
        "required_claim_type": "graph_evidence_unavailable",
        "required_grade": "",
        "required_evidence": ["graph_not_current"],
        "requires_current_structural_evidence": False,
        "decisive_counter_kinds": [],
    },
    "BG-EVIDENCE-001": {
        "title": "Required evidence is incomplete",
        "decision": "incomplete",
        "required_claim_type": "required_evidence_incomplete",
        "required_grade": "",
        "required_evidence": ["required_evidence_unavailable"],
        "requires_current_structural_evidence": False,
        "decisive_counter_kinds": [],
    },
    "BG-TEMPORAL-001": {
        "title": "Historical association is not a blocker",
        "decision": "warn",
        "required_claim_type": "historical_association",
        "required_grade": "",
        "required_evidence": ["historical_association"],
        "requires_current_structural_evidence": False,
        "decisive_counter_kinds": [],
    },
    "BG-AI-001": {
        "title": "Model suggestion is advisory",
        "decision": "warn",
        "required_claim_type": "llm_advisory",
        "required_grade": "",
        "required_evidence": ["model_suggestion"],
        "requires_current_structural_evidence": False,
        "decisive_counter_kinds": [],
    },
    "BG-IMPACT-001": {
        "title": "Cross-service review recommended",
        "decision": "warn",
        "required_claim_type": "cross_service_impact",
        "required_grade": "",
        "required_evidence": ["structural_path"],
        "requires_current_structural_evidence": False,
        "decisive_counter_kinds": [],
    },
}

INVARIANT_SPECS = (
    ("INV-001", "Nami Rank cannot independently block"),
    ("INV-002", "Temporal-only evidence cannot block"),
    ("INV-003", "Model evidence cannot independently block"),
    ("INV-004", "Evidence below the required grade cannot satisfy a verified blocker"),
    ("INV-005", "Contract removal without a known consumer cannot satisfy BG-CONTRACT-001"),
    ("INV-006", "Passed verification cannot satisfy BG-VERIFY-001"),
    ("INV-007", "Stale structural evidence cannot satisfy a current-structure rule"),
    ("INV-008", "priority_score cannot change the Gate decision"),
    ("INV-009", "Decisive counter-evidence cannot coexist with a validated blocking claim"),
    ("INV-010", "A review target cannot independently block"),
)

MAX_COUNTERFACTUALS_PER_RULE = 3

FATAL_DIAGNOSTIC_CODES = frozenset({"invalid_policy", "review_repository_not_allowlisted"})
