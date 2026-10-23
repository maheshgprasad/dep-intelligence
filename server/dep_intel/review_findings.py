"""Turn policy results and NRM candidates into finding claims.

A high NRM score becomes a review target, not a defect.
"""

from __future__ import annotations

import uuid
from typing import Any

from dep_intel.review_models import ORIGIN_STATEMENT, TEMPORAL_LIMIT


def build_findings(policy_results: list[dict[str, Any]], impact: dict[str, Any], change: dict[str, Any], *, model_findings: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for result in policy_results:
        if result.get("result") not in {"violation", "warning", "requested"}:
            continue
        findings.append(_from_policy(result))
    for candidate in impact.get("ranked_candidates") or []:
        findings.append(_from_candidate(candidate))
    for contract in change.get("contract_changes") or []:
        if contract.get("breaking") and not any(item.get("category") == "contract_break" and item.get("file_path") == contract.get("file_path") for item in findings):
            findings.append(_from_contract(contract))
        elif contract.get("kind") == "unknown_schema_change":
            findings.append(_review_target_contract(contract))
    for suggestion in model_findings or []:
        findings.append(_from_model(suggestion))
    return findings


def _from_policy(result: dict[str, Any]) -> dict[str, Any]:
    check = result.get("check")
    category = {
        "forbidden_pattern": "security_risk",
        "required_related_test": "test_gap",
        "required_companion_file": "migration_risk",
        "contract_compatibility": "contract_break",
        "forbidden_dependency": "dependency_risk",
        "required_command": "verification_failure",
        "path_restriction": "policy_violation",
        "documentation_required": "documentation_gap",
    }.get(check, "policy_violation")
    consumers = result.get("consumers") or []
    verified = check in {"forbidden_pattern", "path_restriction", "forbidden_dependency"} or (check == "contract_compatibility" and consumers)
    strength = "verified" if verified else "strong" if result.get("result") == "violation" else "moderate"
    if check == "required_command":
        strength = "moderate"
        category = "verification_failure"
    claim = result.get("evidence") or result.get("description") or ""
    if check == "contract_compatibility" and not consumers:
        claim = f"{claim} No known consumer was found, so this is not treated as a confirmed break."
        strength = "moderate"
        category = "review_target"
    return _finding(
        category=category,
        severity="critical" if result.get("severity") == "critical" and verified else result.get("severity") or "medium",
        title=result.get("description") or result.get("policy_id") or category,
        claim=claim,
        file_path=result.get("file_path"),
        line_start=result.get("line_start"),
        policy_id=result.get("policy_id"),
        source="policy",
        evidence_strength=strength,
        blocking=bool(result.get("blocking")) or (check == "contract_compatibility" and bool(consumers)),
        evidence=[_evidence("policy", "policy", claim, file_path=result.get("file_path"), line_start=result.get("line_start"), data={"policy_id": result.get("policy_id"), "consumers": consumers})],
        structured={"contract": result.get("contract"), "command_id": result.get("command_id")},
    )


def _from_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    origin = candidate.get("evidence_origin") or "structural_only"
    other_service = candidate.get("service_id") and candidate.get("root_service_id") and candidate.get("service_id") != candidate.get("root_service_id")
    category = "cross_repo_impact" if other_service and origin != "temporal_only" else "review_target"
    statement = ORIGIN_STATEMENT.get(origin, ORIGIN_STATEMENT["structural_only"])
    if origin == "temporal_only":
        statement = f"{statement} {TEMPORAL_LIMIT}"
    structural = (candidate.get("structural") or {}).get("score")
    temporal = (candidate.get("temporal") or {}).get("score")
    hybrid = candidate.get("hybrid_score")
    claim = (
        f"{candidate.get('service_id')}:{candidate.get('file_path')} is a review target. {statement} "
        f"structural_score={structural} temporal_score={temporal} hybrid_score={hybrid}. These scores are not probabilities."
    )
    strength = "weak" if origin == "temporal_only" else "moderate" if origin == "structural_only" else "strong"
    return _finding(
        category=category,
        severity="info" if origin == "temporal_only" else "low",
        title=f"Review {candidate.get('file_path') or candidate.get('name')}",
        claim=claim,
        service_id=candidate.get("service_id"),
        repository=candidate.get("repository"),
        file_path=candidate.get("file_path"),
        line_start=candidate.get("line_start"),
        source="NRM",
        evidence_strength=strength,
        blocking=False,
        evidence=[
            _evidence(
                "nrm",
                "NRM",
                statement,
                service_id=candidate.get("service_id"),
                file_path=candidate.get("file_path"),
                data={
                    "evidence_origin": origin,
                    "structural_score": structural,
                    "temporal_score": temporal,
                    "hybrid_score": hybrid,
                    "path": (candidate.get("structural") or {}).get("path") or [],
                    "affected_by_roots": candidate.get("affected_by_roots") or [],
                    "score_note": "Ranking scores are not probabilities.",
                },
            )
        ],
        structured={"evidence_origin": origin, "hybrid_score": hybrid, "rank": candidate.get("rank")},
    )


def _from_contract(contract: dict[str, Any]) -> dict[str, Any]:
    consumers = contract.get("consumers") or []
    claim = contract.get("message") or contract.get("kind") or "Contract change"
    if consumers:
        names = ", ".join(f"{item.get('service_id')}:{item.get('qualified_name')}" for item in consumers)
        claim = f"{claim} Known consumers: {names}."
    return _finding(
        category="contract_break" if consumers else "review_target",
        severity="high" if consumers else "low",
        title=claim,
        claim=claim,
        file_path=contract.get("file_path"),
        line_start=contract.get("line"),
        source="diff",
        evidence_strength="verified" if consumers else "moderate",
        blocking=bool(consumers),
        evidence=[_evidence("contract", "OpenAPI" if contract.get("provenance") == "openapi" else "cluster_graph", claim, file_path=contract.get("file_path"), line_start=contract.get("line"), data=contract)],
    )


def _review_target_contract(contract: dict[str, Any]) -> dict[str, Any]:
    return _finding(
        category="review_target",
        severity="low",
        title="Unrecognized contract schema change",
        claim=contract.get("message") or "The contract changed in a way compatibility analysis could not classify.",
        file_path=contract.get("file_path"),
        source="diff",
        evidence_strength="moderate",
        blocking=False,
        evidence=[_evidence("contract", "OpenAPI", contract.get("message") or "", file_path=contract.get("file_path"), data=contract)],
    )


def _from_model(suggestion: dict[str, Any]) -> dict[str, Any]:
    return _finding(
        category=suggestion.get("category") or "regression_risk",
        severity=suggestion.get("severity") or "low",
        title=suggestion.get("claim") or "Model suggestion",
        claim=suggestion.get("claim") or "",
        file_path=suggestion.get("file_path"),
        line_start=suggestion.get("line_start"),
        source="LLM",
        evidence_strength="weak",
        blocking=False,
        evidence=[_evidence("model", "LLM", suggestion.get("claim") or "", file_path=suggestion.get("file_path"), line_start=suggestion.get("line_start"), data={"evidence_refs": suggestion.get("evidence_refs") or []})],
        structured={"evidence_refs": suggestion.get("evidence_refs") or [], "advisory": True},
    )


def _finding(**kwargs: Any) -> dict[str, Any]:
    body = {
        "finding_id": uuid.uuid4().hex,
        "category": "review_target",
        "severity": "info",
        "title": "",
        "claim": "",
        "service_id": "",
        "repository": "",
        "file_path": None,
        "line_start": None,
        "line_end": None,
        "source": "policy",
        "policy_id": None,
        "evidence_strength": "moderate",
        "blocking": False,
        "evidence": [],
        "verification_status": "not_requested",
        "structured": {},
    }
    body.update(kwargs)
    return body


def _evidence(kind: str, source: str, summary: str, **extra: Any) -> dict[str, Any]:
    return {
        "evidence_id": uuid.uuid4().hex,
        "kind": kind,
        "source": source,
        "summary": summary,
        "service_id": extra.get("service_id"),
        "repository": extra.get("repository"),
        "file_path": extra.get("file_path"),
        "line_start": extra.get("line_start"),
        "line_end": extra.get("line_end"),
        "graph_version": extra.get("graph_version"),
        "base_sha": extra.get("base_sha"),
        "head_sha": extra.get("head_sha"),
        "structured_data": extra.get("data") or {},
        "stale": bool(extra.get("stale")),
    }
