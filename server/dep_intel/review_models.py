"""Typed records for Nami Boundary Gate.

These records describe a review. They do not change Nami Rank scores.
"""

from __future__ import annotations

from typing import Any, Literal

NBG_VERSION = "1.0"
MODULE_NAME = "Nami Boundary Gate"
MODULE_ABBREV = "NBG"
SCHEMA_VERSION = 1

ChangeKind = Literal["working_tree", "staged", "commit", "range", "github_pr", "explicit"]
Alignment = Literal["exact", "compatible", "stale", "unavailable"]
DecisionName = Literal["pass", "warn", "block", "incomplete"]
EvidenceGrade = Literal["verified", "strong", "moderate", "weak", "unsupported"]
SeverityName = Literal["critical", "high", "medium", "low", "info"]

SCORE_NOTE = "Ranking scores are not probabilities of breakage, change, or failure."

CATEGORIES = (
    "contract_break",
    "cross_repo_impact",
    "test_gap",
    "policy_violation",
    "security_risk",
    "dependency_risk",
    "configuration_risk",
    "migration_risk",
    "regression_risk",
    "verification_failure",
    "stale_evidence",
    "documentation_gap",
    "review_target",
)

SEVERITY_WEIGHT = {"critical": 100, "high": 70, "medium": 40, "low": 20, "info": 5}
EVIDENCE_BONUS = {"verified": 20, "strong": 12, "moderate": 6, "weak": 0, "unsupported": 0}

ORIGIN_STATEMENT = {
    "structural_only": "Structurally reachable from the changed code; no relevant temporal evidence was available.",
    "temporal_only": "Historically associated with the changed file, but not structurally reachable in the current graph.",
    "both": "Supported by structural reachability and historical directional evidence.",
}

TEMPORAL_LIMIT = "Historical association does not establish a dependency or a causal failure."

DEFAULT_BUDGETS = {
    "max_changed_files": 200,
    "max_diff_bytes": 1024 * 1024,
    "max_context_files": 50,
    "max_context_bytes_per_file": 32 * 1024,
    "max_total_context_bytes": 512 * 1024,
    "max_structural_candidates": 100,
    "max_nrm_candidates": 50,
    "max_policy_matches": 100,
    "max_findings": 20,
    "max_inline_findings": 12,
    "git_timeout_seconds": 30,
    "max_git_output_bytes": 1024 * 1024,
}


def priority_score(
    severity: str,
    evidence: str,
    *,
    cross_repository: bool,
    nrm_rank: int | None,
    nrm_count: int,
) -> int:
    """Presentation order only. The gate does not decide from this number."""
    base = SEVERITY_WEIGHT.get(severity, 0) + EVIDENCE_BONUS.get(evidence, 0)
    if cross_repository:
        base += 10
    if nrm_rank and nrm_count > 0:
        span = max(nrm_count - 1, 1)
        base += round(10 * (1 - ((nrm_rank - 1) / span)))
    return base


def diagnostic(code: str, message: str, *, severity: str = "warning", **extra: Any) -> dict[str, Any]:
    body = {"code": code, "message": message, "severity": severity}
    body.update(extra)
    return body
