"""Central evidence grades.

Finding generators may propose a candidate strength. This module is the
authority for the final grade. It can downgrade or suppress. It does not
raise a candidate to verified on its own, and it never treats a ranking
score as evidence.
"""

from __future__ import annotations

import logging
from typing import Any

from dep_intel.review_evidence_requirements import CLAIM_REQUIREMENTS, DECISIVE_COUNTERS
from dep_intel.review_models import diagnostic

log = logging.getLogger("nami.boundary")

GRADE_ORDER = {"unsupported": 0, "weak": 1, "moderate": 2, "strong": 3, "verified": 4}


def claim_type_for(finding: dict[str, Any]) -> str | None:
    origin = (finding.get("structured") or {}).get("evidence_origin")
    if finding.get("source") == "LLM":
        return "llm_advisory"
    if origin == "temporal_only":
        return "historical_association"
    category = finding.get("category") or ""
    if category == "contract_break":
        return "consumed_contract_break"
    if category == "verification_failure" and finding.get("verification_status") == "failed":
        return "required_verification_failure"
    if category == "test_gap":
        return "test_gap"
    if category in {"security_risk", "policy_violation", "dependency_risk"} and finding.get("evidence_strength") == "verified":
        return "forbidden_pattern_violation"
    if finding.get("severity") == "critical" and finding.get("evidence_strength") == "verified" and finding.get("blocking"):
        return "verified_critical"
    return None


def grade_claim(claim: dict[str, Any], evidence: list[dict[str, Any]], *, alignment: str = "exact") -> dict[str, Any]:
    """Return the final grade for one claim. Confidence here is a grade, not a probability."""
    claim_type = str(claim.get("type") or "")
    spec = CLAIM_REQUIREMENTS.get(claim_type, {})
    required = list(spec.get("required") or [])
    supporting = [
        item
        for item in evidence
        if item.get("kind") in required or claim_type in (item.get("supports") or [])
    ]
    counters = [
        item
        for item in evidence
        if claim_type in (item.get("contradicts") or []) or item.get("kind") in DECISIVE_COUNTERS.get(claim_type, set())
    ]
    decisive = [item for item in counters if item.get("kind") in DECISIVE_COUNTERS.get(claim_type, set()) or claim_type in (item.get("contradicts") or [])]
    if decisive:
        reason = decisive[0].get("statement") or "Counter-evidence contradicts this claim."
        log.info("counter_evidence.detected", extra={"claim_type": claim_type, "kind": decisive[0].get("kind")})
        log.info("evidence.rejected", extra={"claim_type": claim_type, "status": "suppressed"})
        return {
            "grade": "unsupported",
            "status": "suppressed",
            "reason": reason,
            "missing": [],
            "counter_evidence": decisive,
        }
    if claim_type == "llm_advisory" or (supporting and all(item.get("source") == "LLM" for item in supporting) and not required):
        log.info("evidence.rejected", extra={"claim_type": claim_type, "status": "advisory"})
        return {"grade": "weak", "status": "advisory", "reason": "A model suggestion is not gate evidence.", "missing": [], "counter_evidence": []}
    missing = [kind for kind in required if not any(item.get("kind") == kind for item in supporting)]
    if missing:
        grade = "weak" if supporting else "unsupported"
        log.info("evidence.rejected", extra={"claim_type": claim_type, "status": "downgraded"})
        return {"grade": grade, "status": "downgraded", "reason": "Required evidence is missing.", "missing": missing, "counter_evidence": []}
    if spec.get("requires_current_structural_evidence") and alignment in {"stale", "unavailable"}:
        grade = "moderate" if alignment == "stale" else "weak"
        log.info("evidence.rejected", extra={"claim_type": claim_type, "status": "stale"})
        return {
            "grade": grade,
            "status": "downgraded",
            "reason": "Structural evidence is not current for this revision.",
            "missing": [],
            "counter_evidence": [],
        }
    if spec.get("max_grade"):
        log.info("evidence.accepted", extra={"claim_type": claim_type, "grade": spec["max_grade"]})
        return {"grade": spec["max_grade"], "status": "accepted", "reason": "", "missing": [], "counter_evidence": []}
    if not required and claim_type not in CLAIM_REQUIREMENTS:
        return {"grade": None, "status": "unchanged", "reason": "", "missing": [], "counter_evidence": []}
    if not required:
        return {"grade": None, "status": "unchanged", "reason": "", "missing": [], "counter_evidence": []}
    log.info("evidence.accepted", extra={"claim_type": claim_type, "grade": "verified"})
    return {"grade": "verified", "status": "accepted", "reason": "", "missing": [], "counter_evidence": []}


def apply_claim_sufficiency(findings: list[dict[str, Any]], *, alignment: str = "exact") -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Downgrade or suppress accepted findings. Does not raise candidate grades."""
    kept: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for finding in findings:
        candidate = finding.get("evidence_strength") or "weak"
        claim_type = claim_type_for(finding)
        stamped = {**finding, "candidate_strength": candidate}
        if claim_type is None:
            kept.append(stamped)
            continue
        evidence = materialize_evidence(stamped, claim_type)
        graded = grade_claim({"type": claim_type}, evidence, alignment=alignment)
        if graded["status"] == "suppressed":
            rejected.append(
                diagnostic(
                    "evidence_rejected",
                    graded["reason"],
                    severity="info",
                    finding_id=finding.get("finding_id"),
                    category=finding.get("category"),
                    suppressed=True,
                    claim=finding.get("claim") or finding.get("title") or "",
                    counter_statement=graded["reason"],
                )
            )
            continue
        final = graded.get("grade")
        if isinstance(final, str) and GRADE_ORDER.get(final, 0) < GRADE_ORDER.get(candidate, 1):
            stamped = {**stamped, "evidence_strength": final, "evidence_grade": final, "sufficiency": graded}
        else:
            stamped = {**stamped, "evidence_grade": candidate, "sufficiency": graded}
        kept.append(stamped)
    return kept, rejected


def materialize_evidence(finding: dict[str, Any], claim_type: str) -> list[dict[str, Any]]:
    """Read evidence the finding already carries. Missing fields stay absent."""
    items: list[dict[str, Any]] = []
    structured = finding.get("structured") or {}
    contract = structured.get("contract") if isinstance(structured.get("contract"), dict) else {}
    consumers = list(structured.get("consumers") or contract.get("consumers") or [])
    for row in finding.get("evidence") or []:
        data = row.get("structured_data") if isinstance(row.get("structured_data"), dict) else {}
        if data.get("consumers"):
            consumers = list(data["consumers"])
        nested = data.get("contract")
        if isinstance(nested, dict) and nested.get("consumers"):
            consumers = list(nested["consumers"])
    if claim_type == "consumed_contract_break":
        items.append(_item("contract_removed", "diff", finding, claim_type, finding.get("claim") or "A contract operation was removed."))
        if consumers:
            name = consumers[0].get("service_id") or "a known consumer"
            items.append(_item("known_consumer", "cluster_graph", finding, claim_type, f"{name} consumes the removed contract."))
    elif claim_type == "required_verification_failure":
        items.append(_item("verification_executed", "verification_command", finding, claim_type, "The required verification command ran."))
        items.append(_item("verification_failed", "verification_command", finding, claim_type, finding.get("claim") or "The required verification command failed."))
    elif claim_type in {"forbidden_pattern_violation", "verified_critical"}:
        items.append(_item("policy_match", "policy", finding, claim_type, finding.get("claim") or "A configured policy matched."))
        if finding.get("file_path"):
            items.append(_item("changed_added_line", "diff", finding, claim_type, "The match is on a changed line."))
    elif claim_type == "historical_association":
        items.append(_item("historical_association", "git_history", finding, claim_type, finding.get("claim") or "Historical association only."))
    elif claim_type == "llm_advisory":
        items.append(_item("model_suggestion", "LLM", finding, claim_type, finding.get("claim") or "Model suggestion."))
    for extra in finding.get("counter_evidence") or []:
        if isinstance(extra, dict):
            items.append(extra)
    return items


def _item(kind: str, source: str, finding: dict[str, Any], claim_type: str, statement: str) -> dict[str, Any]:
    return {
        "kind": kind,
        "source": source,
        "statement": statement,
        "supports": [claim_type],
        "contradicts": [],
        "file_path": finding.get("file_path"),
        "line_start": finding.get("line_start"),
        "service_id": finding.get("service_id"),
        "freshness": "stale" if any(item.get("stale") for item in finding.get("evidence") or []) else "current",
    }
