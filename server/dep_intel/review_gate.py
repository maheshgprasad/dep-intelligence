"""Boundary decision: pass, warn, block, or incomplete.

priority_score orders findings. It is not an input to this decision.
"""

from __future__ import annotations

from typing import Any

from dep_intel.review_models import priority_score


def decide_gate(
    findings: list[dict[str, Any]],
    verification: list[dict[str, Any]],
    config: dict[str, Any],
    *,
    alignment: str,
    diagnostics: list[dict[str, Any]],
) -> dict[str, Any]:
    gate = config.get("gate") or {}
    review = config.get("review") or {}
    max_findings = int(review.get("max_findings") or 20)
    ranked = _rank(findings)
    blocking = []
    warnings = []
    targets = []
    reason_codes: list[str] = []
    required_actions: list[str] = []
    evidence_complete = True

    fatal = [item for item in diagnostics if item.get("severity") == "error" and item.get("code") in {"invalid_policy", "review_repository_not_allowlisted"}]
    required_failed = [item for item in verification if item.get("required") and item.get("status") in {"timeout", "skipped", "failed"}]
    unavailable_required = [item for item in verification if item.get("required") and item.get("status") in {"timeout", "skipped"}]
    if gate.get("strict_graph") and alignment in {"stale", "unavailable"}:
        evidence_complete = False
        reason_codes.append("graph_stale" if alignment == "stale" else "graph_missing")
    if gate.get("incomplete_on_required_verification_failure", True) and unavailable_required:
        evidence_complete = False
        reason_codes.append("verification_unavailable")
    if fatal or (not evidence_complete and (gate.get("strict_graph") or unavailable_required)):
        return _decision("incomplete", reason_codes or ["evidence_incomplete"], [], ranked, ranked, required_actions, False, diagnostics)

    for finding in ranked:
        if _blocks(finding, gate):
            blocking.append(finding)
            code = finding.get("category") or "blocking_finding"
            if code not in reason_codes:
                reason_codes.append(code)
            required_actions.append(finding.get("title") or finding.get("claim") or code)
        elif finding.get("category") == "review_target":
            targets.append(finding)
            if "review_target" not in reason_codes and finding.get("structured", {}).get("evidence_origin") != "temporal_only":
                reason_codes.append("review_target")
        else:
            warnings.append(finding)
            code = finding.get("category") or "warning"
            if code not in reason_codes:
                reason_codes.append(code)

    if any(item.get("structured", {}).get("evidence_origin") == "temporal_only" for item in targets):
        if "temporal_only_review_target" not in reason_codes:
            reason_codes.append("temporal_only_review_target")
    if alignment == "stale" and "stale_evidence" not in reason_codes:
        warnings.append({
            "finding_id": "stale-graph",
            "category": "stale_evidence",
            "severity": "low",
            "title": "Graph alignment is stale",
            "claim": "The published graph does not match the reviewed revision. Structural evidence was downgraded.",
            "evidence_strength": "moderate",
            "blocking": False,
            "evidence": [],
            "fingerprint": "stale-graph",
        })
        reason_codes.append("stale_evidence")

    shown_blocking = blocking
    budgeted = warnings[:max_findings]
    overflow = warnings[max_findings:]
    if overflow:
        reason_codes.append("finding_budget")
    if blocking:
        decision = "block"
    elif warnings or targets or alignment == "stale":
        decision = "warn"
    else:
        decision = "pass"
        reason_codes = []
    return _decision(decision, reason_codes, shown_blocking, budgeted, targets, required_actions, evidence_complete, diagnostics, overflow=len(overflow))


def _blocks(finding: dict[str, Any], gate: dict[str, Any]) -> bool:
    origin = (finding.get("structured") or {}).get("evidence_origin")
    if origin == "temporal_only" or finding.get("category") == "review_target":
        return False
    if finding.get("source") == "LLM":
        return False
    strength = finding.get("evidence_strength")
    if strength != "verified":
        return False
    if finding.get("severity") == "critical" and gate.get("block_on_verified_critical", True):
        return True
    if finding.get("blocking") and finding.get("severity") in {"critical", "high"} and gate.get("block_on_verified_high_blocker_policy", True):
        return True
    if finding.get("category") == "contract_break" and gate.get("block_on_breaking_contract_with_known_consumers", True):
        return True
    if finding.get("category") == "verification_failure" and finding.get("verification_status") == "failed":
        return True
    return False


def _rank(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    count = len(findings) or 1
    decorated = []
    for index, finding in enumerate(findings, start=1):
        cross = finding.get("category") == "cross_repo_impact"
        rank = (finding.get("structured") or {}).get("rank") or index
        score = priority_score(
            finding.get("severity") or "info",
            finding.get("evidence_strength") or "weak",
            cross_repository=cross,
            nrm_rank=rank if finding.get("source") == "NRM" else None,
            nrm_count=count,
        )
        decorated.append({**finding, "priority_score": score, "priority_note": "priority_score controls presentation order only."})
    decorated.sort(key=lambda item: (-item["priority_score"], item.get("file_path") or "", item.get("title") or ""))
    return decorated


def _decision(
    decision: str,
    reason_codes: list[str],
    blocking: list[dict],
    warnings: list[dict],
    targets: list[dict],
    actions: list[str],
    complete: bool,
    diagnostics: list[dict],
    overflow: int = 0,
) -> dict[str, Any]:
    return {
        "decision": decision,
        "reason_codes": reason_codes,
        "blocking_findings": [item.get("finding_id") for item in blocking],
        "warning_findings": [item.get("finding_id") for item in warnings],
        "review_targets": [item.get("finding_id") for item in targets],
        "required_actions": actions,
        "evidence_complete": complete,
        "findings": blocking + warnings + targets,
        "suppressed_count": overflow,
        "diagnostics": diagnostics,
    }
