"""Labelled Boundary Gate scenarios.

Expected decisions are the gate outcomes for these stored inputs.
They are not copied from the validator.
"""

from __future__ import annotations

from typing import Any

_GATE = {
    "strict_graph": False,
    "incomplete_on_required_verification_failure": True,
    "block_on_verified_critical": True,
    "block_on_verified_high_blocker_policy": True,
    "block_on_breaking_contract_with_known_consumers": True,
}


def _change(alignment: str = "exact") -> dict[str, Any]:
    return {
        "repository": "auth-service",
        "service_id": "auth-service",
        "base_sha": "basesha",
        "head_sha": "headsha",
        "graph_alignment": alignment,
        "graph_version": "graph-1",
    }


def _contract(consumers: list[dict[str, str]] | None) -> dict[str, Any]:
    names = consumers or []
    return {
        "finding_id": "contract-1",
        "category": "contract_break" if names else "review_target",
        "severity": "high" if names else "low",
        "title": "Removed POST /api/auth/validate",
        "claim": "POST /api/auth/validate was removed.",
        "evidence_strength": "verified" if names else "moderate",
        "blocking": bool(names),
        "source": "diff",
        "file_path": "src/server.js",
        "line_start": 4,
        "service_id": "auth-service",
        "repository": "auth-service",
        "verification_status": "not_requested",
        "structured": {"contract": {"kind": "operation_removed", "consumers": names}},
        "evidence": [{
            "kind": "contract",
            "source": "cluster_graph",
            "summary": "POST /api/auth/validate was removed.",
            "file_path": "src/server.js",
            "line_start": 4,
            "structured_data": {
                "kind": "operation_removed",
                "method": "POST",
                "path": "/api/auth/validate",
                "breaking": True,
                "consumers": names,
            },
        }],
    }


def _impact(origin: str, category: str = "review_target") -> dict[str, Any]:
    return {
        "finding_id": f"impact-{origin}",
        "category": category,
        "severity": "info" if origin == "temporal_only" else "low",
        "title": "Review src/users.js",
        "claim": "user-service:src/users.js is a review target.",
        "evidence_strength": "weak" if origin == "temporal_only" else "moderate",
        "blocking": False,
        "source": "NRM",
        "file_path": "src/users.js",
        "line_start": 6,
        "service_id": "user-service",
        "repository": "user-service",
        "structured": {"evidence_origin": origin, "hybrid_score": 0.91, "rank": 1},
        "evidence": [{
            "kind": "nrm",
            "source": "NRM",
            "summary": "Review target.",
            "file_path": "src/users.js",
            "service_id": "user-service",
            "structured_data": {"evidence_origin": origin, "hybrid_score": 0.91},
        }],
    }


def _verification_finding(status: str, exit_code: int | None) -> dict[str, Any]:
    return {
        "finding_id": "verify-auth-tests",
        "category": "verification_failure",
        "severity": "high",
        "title": "Verification auth-tests failed",
        "claim": f"Required verification auth-tests failed with exit code {exit_code}.",
        "evidence_strength": "verified" if status == "failed" else "moderate",
        "blocking": status == "failed",
        "source": "verification_command",
        "verification_status": status,
        "file_path": None,
        "structured": {"command_id": "auth-tests"},
        "evidence": [{
            "kind": "verification_failed" if status == "failed" else "verification_executed",
            "source": "verification_command",
            "summary": f"Verification auth-tests {status}.",
        }],
    }


def _command(status: str, exit_code: int | None, required: bool = True) -> dict[str, Any]:
    return {"id": "auth-tests", "required": required, "status": status, "exit_code": exit_code, "command": ["python"], "cwd": "."}


def _policy(severity: str) -> dict[str, Any]:
    return {
        "finding_id": f"policy-{severity}",
        "category": "security_risk" if severity == "critical" else "policy_violation",
        "severity": severity,
        "title": "Forbidden marker",
        "claim": "A configured forbidden marker is on a changed line.",
        "evidence_strength": "verified",
        "blocking": True,
        "source": "policy",
        "policy_id": "no-secrets",
        "file_path": "src/server.js",
        "line_start": 8,
        "structured": {},
        "evidence": [{
            "kind": "policy",
            "source": "policy",
            "summary": "A configured forbidden marker is on a changed line.",
            "file_path": "src/server.js",
            "line_start": 8,
            "structured_data": {"policy_id": "no-secrets"},
        }],
    }


def _scenario(name: str, expected_decision: str, expected_rules: list[str], **kwargs: Any) -> dict[str, Any]:
    gate = dict(_GATE)
    gate.update(kwargs.pop("gate", {}))
    return {
        "name": name,
        "expected_decision": expected_decision,
        "expected_rules": expected_rules,
        "findings": kwargs.get("findings") or [],
        "verification": kwargs.get("verification") or [],
        "diagnostics": kwargs.get("diagnostics") or [],
        "change": kwargs.get("change") or _change(),
        "contract_changes": kwargs.get("contract_changes") or [],
        "impact": kwargs.get("impact") or {"affected_services": [], "ranked_candidates": []},
        "config": {"gate": gate, "review": {"max_findings": 20}, "enforcement": {"mode": "advisory"}},
    }


def scenarios() -> list[dict[str, Any]]:
    """Hand-labelled inputs. expected_decision follows the gate conditions, not the validator."""
    return [
        _scenario("consumed-contract-removal", "block", ["BG-CONTRACT-001"], findings=[_contract([{"service_id": "user-service"}])], impact={"affected_services": ["user-service"], "ranked_candidates": []}, contract_changes=[{"kind": "operation_removed", "method": "POST", "path": "/api/auth/validate", "breaking": True, "consumers": [{"service_id": "user-service"}], "file_path": "src/server.js"}]),
        _scenario("contract-removal-without-consumer", "warn", [], findings=[_contract([])], contract_changes=[{"kind": "operation_removed", "method": "POST", "path": "/api/auth/validate", "breaking": True, "consumers": [], "file_path": "src/server.js"}]),
        _scenario("body-only-handler-change", "warn", ["BG-IMPACT-001"], findings=[_impact("structural_only", "cross_repo_impact")], impact={"affected_services": ["user-service"], "ranked_candidates": []}),
        _scenario("temporal-only-target", "warn", ["BG-TEMPORAL-001"], findings=[_impact("temporal_only")], impact={"affected_services": ["user-service"], "ranked_candidates": []}),
        _scenario("llm-only-concern", "warn", ["BG-AI-001"], findings=[{
            "finding_id": "llm-1",
            "category": "regression_risk",
            "severity": "low",
            "title": "Model note",
            "claim": "Review the changed handler.",
            "evidence_strength": "weak",
            "blocking": False,
            "source": "LLM",
            "file_path": "src/server.js",
            "line_start": 4,
            "structured": {"advisory": True},
            "evidence": [{"kind": "model", "source": "LLM", "summary": "Review the changed handler.", "file_path": "src/server.js"}],
        }]),
        _scenario("required-verification-failed", "block", ["BG-VERIFY-001"], findings=[_verification_finding("failed", 3)], verification=[_command("failed", 3)]),
        _scenario("required-verification-passed", "pass", [], verification=[_command("passed", 0)]),
        _scenario("verified-policy-violation", "block", ["BG-POLICY-001"], findings=[_policy("high")]),
        _scenario("verified-critical-policy", "block", ["BG-CRITICAL-001", "BG-POLICY-001"], findings=[_policy("critical")]),
        _scenario("stale-graph-strict", "incomplete", ["BG-GRAPH-001"], change=_change("stale"), gate={"strict_graph": True}),
        _scenario("stale-graph-non-strict", "warn", [], change=_change("stale")),
        _scenario("counter-evidence-suppressing-contract-removal", "pass", [], diagnostics=[{"code": "evidence_rejected", "message": "The claimed removed API is still present.", "severity": "info", "suppressed": True, "category": "contract_break", "finding_id": "suppressed-contract", "claim": "Removed POST /api/auth/validate"}]),
        _scenario("clean-safe-change", "pass", []),
        _scenario("required-verification-unavailable", "incomplete", ["BG-EVIDENCE-001"], verification=[_command("skipped", None)]),
        _scenario("high-rank-review-target", "warn", ["BG-IMPACT-001"], findings=[_impact("structural_only")], impact={"affected_services": ["user-service"], "ranked_candidates": []}),
        _scenario("unavailable-graph-strict", "incomplete", ["BG-GRAPH-001"], change=_change("unavailable"), gate={"strict_graph": True}),
        _scenario("nrm-only-high-rank", "warn", ["BG-IMPACT-001"], findings=[_impact("structural_only")], impact={"affected_services": ["user-service"], "ranked_candidates": []}),
        _scenario("failed-optional-verification", "pass", [], verification=[{**_command("failed", 1), "required": False}]),
        _scenario("multiple-consumers", "block", ["BG-CONTRACT-001"], findings=[_contract([{"service_id": "user-service"}, {"service_id": "billing-service"}])], impact={"affected_services": ["user-service", "billing-service"], "ranked_candidates": []}),
        _scenario("policy-match-on-deleted-line", "warn", [], findings=[{
            **_policy("high"),
            "evidence_strength": "moderate",
            "blocking": False,
            "claim": "The forbidden pattern appears only on a deleted line.",
        }]),
    ]
