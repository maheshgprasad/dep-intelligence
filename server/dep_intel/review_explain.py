"""Explain a stored Boundary Gate decision.

This layer does not choose pass, warn, block, or incomplete. decide_gate does.
Nami Rank scores are not read as a reason to block.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("nami.boundary")

GATE_RULES: dict[str, dict[str, str]] = {
    "BG-CONTRACT-001": {"title": "Breaking contract with known consumers", "decision": "block"},
    "BG-VERIFY-001": {"title": "Required verification failed", "decision": "block"},
    "BG-POLICY-001": {"title": "Verified blocking policy violation", "decision": "block"},
    "BG-CRITICAL-001": {"title": "Verified critical evidence", "decision": "block"},
    "BG-GRAPH-001": {"title": "Required graph evidence unavailable", "decision": "incomplete"},
    "BG-EVIDENCE-001": {"title": "Required evidence is incomplete", "decision": "incomplete"},
    "BG-TEMPORAL-001": {"title": "Historical association is not a blocker", "decision": "warn"},
    "BG-AI-001": {"title": "Model suggestion is advisory", "decision": "warn"},
    "BG-IMPACT-001": {"title": "Cross-service review recommended", "decision": "warn"},
}

PRESENTATION = {
    "pass": {
        "heading": "Ready to proceed",
        "label": "PASS",
        "note": "Evidence complete. Required checks that ran passed. No verified blocker.",
    },
    "warn": {
        "heading": "Review recommended",
        "label": "WARN",
        "note": "No verified blocker. Review work remains.",
    },
    "block": {
        "heading": "Action required before proceeding",
        "label": "BLOCK",
        "note": "A verified blocker triggered a gate rule. A required action is listed.",
    },
    "incomplete": {
        "heading": "More evidence is needed",
        "label": "INCOMPLETE",
        "note": "Evidence is unavailable or inconclusive, so the decision cannot be completed.",
    },
}

ALIGNMENT_LABELS = {
    "exact": "Exact match",
    "compatible": "Current enough",
    "stale": "Out of date",
    "unavailable": "Unavailable",
}

SOURCE_LABELS = {
    "diff": "Code diff",
    "CRG": "Code graph",
    "cluster_graph": "Cross-service dependency graph",
    "manifest": "Service manifest",
    "OpenAPI": "OpenAPI",
    "git_history": "Repository history",
    "NRM": "Nami Rank",
    "test": "Tests",
    "verification_command": "Verification",
    "policy": "Project policy",
    "LLM": "Model suggestion",
}

INFERENCE_TITLES = {
    "consumer_compatibility_break": "Known consumer may be incompatible",
    "consumer_compatibility_review": "Contract change needs a compatibility look",
    "cross_repository_review_required": "Another service should be reviewed",
    "structural_and_historical_support": "Structural and historical evidence agree",
    "historical_association_only": "Historically associated review target",
    "structural_dependency_confirmed": "Current dependency confirmed",
    "verification_failed": "Required verification failed",
    "verification_required": "Verification still needs to run",
    "test_attention_required": "Tests need attention",
    "wide_blast_radius": "Several downstream services are in scope",
    "stale_evidence": "Dependency evidence is out of date",
    "incomplete_evidence": "A required check could not be completed",
    "policy_violation": "A project rule matched this change",
    "security_attention_required": "A configured security rule matched",
    "deployment_attention_required": "This change needs attention before deployment",
}


def attach_explanation(report: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Add assessment, evidence, inferences, checklist, and actions. Existing fields stay."""
    body = dict(report)
    options = config or {}
    evidence, counter = catalog_evidence(body)
    by_finding = _index_evidence(evidence)
    mode = str((options.get("enforcement") or {}).get("mode") or "advisory") if isinstance(options.get("enforcement"), dict) else "advisory"
    inferences = build_inferences(body, by_finding, enforcement_mode=mode)
    actions = build_actions(body, inferences)
    rules = build_decision_rules(body, inferences, evidence, actions)
    checklist = build_checklist(body, evidence)
    assessment = build_assessment(body, inferences, actions)
    enforcement = enforcement_record((body.get("gate") or {}).get("decision") or "incomplete", options)
    gate = dict(body.get("gate") or {})
    gate["decision_rules"] = rules
    gate["summary"] = assessment["summary"]
    body["gate"] = gate
    body["assessment"] = assessment
    body["evidence_catalog"] = evidence
    body["counter_evidence"] = counter
    body["inferences"] = inferences
    body["gate_checklist"] = checklist
    body["actions"] = actions
    body["suppressed_findings"] = suppressed_findings(body, counter)
    body["enforcement"] = enforcement
    body["decision_presentation"] = presentation(gate.get("decision") or "incomplete")
    body["copy_summary"] = copy_summary(body)
    for rule in rules:
        if rule.get("outcome") == "triggered":
            log.info("gate_rule.triggered", extra={"rule_id": rule.get("rule_id"), "decision": gate.get("decision")})
    for item in inferences:
        log.info("inference.created", extra={"inference_id": item.get("inference_id"), "type": item.get("type")})
    return body


def presentation(decision: str) -> dict[str, str]:
    chosen = PRESENTATION.get(decision, PRESENTATION["incomplete"])
    return {"decision": decision, **chosen}


def enforcement_record(decision: str, config: dict[str, Any]) -> dict[str, Any]:
    block = config.get("enforcement") if isinstance(config.get("enforcement"), dict) else {}
    mode = str(block.get("mode") or "advisory")
    if mode not in {"advisory", "merge_gate", "deployment_gate"}:
        mode = "advisory"
    incomplete = str(block.get("incomplete") or "hold")
    if incomplete not in {"hold", "allow"}:
        incomplete = "hold"
    exit_code = enforcement_exit(decision, mode, incomplete)
    prevented = exit_code != 0
    return {
        "gate_decision": decision,
        "enforcement_mode": mode,
        "incomplete_policy": incomplete,
        "enforced": mode != "advisory",
        "workflow_prevented": prevented,
        "exit_code": exit_code,
    }


def enforcement_exit(decision: str, mode: str, incomplete: str = "hold") -> int:
    """How automation should treat the decision. The gate value itself does not change."""
    if mode == "advisory":
        return 0
    if decision in {"pass", "warn"}:
        return 0
    if decision == "block":
        return 1
    if incomplete == "allow":
        return 0
    return 2


def catalog_evidence(report: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    change = report.get("change") or {}
    raw: list[dict[str, Any]] = []
    counter: list[dict[str, Any]] = []
    for finding in report.get("findings") or []:
        rows = finding.get("evidence") or []
        if not rows:
            rows = [{"kind": finding.get("category") or "finding", "source": finding.get("source") or "diff", "summary": finding.get("claim") or finding.get("title") or ""}]
        for row in rows:
            raw.append(_evidence_from_row(row, finding, change))
    for item in report.get("verification") or []:
        if item.get("status") in {"passed", "failed", "timeout", "skipped"}:
            raw.append(_verification_evidence(item, change))
    alignment = change.get("graph_alignment")
    if alignment:
        raw.append({
            "kind": "graph_alignment",
            "source": "cluster_graph",
            "statement": _alignment_statement(str(alignment)),
            "service_id": change.get("service_id"),
            "repository": change.get("repository"),
            "file_path": None,
            "line_start": None,
            "line_end": None,
            "base_sha": change.get("base_sha"),
            "head_sha": change.get("head_sha"),
            "graph_version": change.get("graph_version"),
            "freshness": "current" if alignment in {"exact", "compatible"} else "stale" if alignment == "stale" else "unavailable",
            "supports": ["gate_check"],
            "contradicts": [],
            "structured_data": {"alignment": alignment},
            "provenance": {"extractor": "graph_alignment", "observed_at": None},
            "finding_id": "",
        })
    for item in report.get("diagnostics") or []:
        if item.get("suppressed") or "still present" in str(item.get("message") or "").lower():
            counter.append({
                "kind": "counter_evidence",
                "source": "diff",
                "statement": item.get("counter_statement") or item.get("message") or "Counter-evidence was recorded.",
                "supports": [],
                "contradicts": [item.get("category") or "claim"],
                "finding_id": item.get("finding_id") or "",
                "claim": item.get("claim") or item.get("message") or "",
                "freshness": "current",
                "service_id": change.get("service_id"),
                "repository": change.get("repository"),
                "file_path": None,
                "line_start": None,
                "line_end": None,
                "base_sha": change.get("base_sha"),
                "head_sha": change.get("head_sha"),
                "graph_version": change.get("graph_version"),
                "structured_data": {},
                "provenance": {"extractor": "evidence_gate", "observed_at": None},
            })
    ordered = _assign_ids(raw, "EV")
    counter_ids = _assign_ids(counter, "EV", start=len(ordered) + 1)
    return ordered, counter_ids


def build_inferences(report: dict[str, Any], evidence_by_finding: dict[str, list[str]], *, enforcement_mode: str = "advisory") -> list[dict[str, Any]]:
    change = report.get("change") or {}
    root = change.get("service_id") or ""
    gate = report.get("gate") or {}
    blocking = set(gate.get("blocking_findings") or [])
    created: list[dict[str, Any]] = []
    services = [item for item in (report.get("impact") or {}).get("affected_services") or [] if item and item != root]
    if len(services) >= 3:
        created.append(_inference("wide_blast_radius", "info", "weak", f"This change reaches {len(services)} downstream services.", services, []))
    for finding in report.get("findings") or []:
        category = finding.get("category")
        origin = (finding.get("structured") or {}).get("evidence_origin")
        refs = evidence_by_finding.get(finding.get("finding_id") or "", [])
        service = finding.get("service_id") or ""
        if category == "contract_break" and finding.get("finding_id") in blocking:
            consumer = _consumer_name(finding) or "a known consumer"
            created.append(_inference(
                "consumer_compatibility_break",
                "high",
                finding.get("evidence_grade") or finding.get("evidence_strength") or "verified",
                f"{consumer} depends on a removed contract from {root or 'the changed service'}.",
                [consumer] if consumer != "a known consumer" else [],
                refs,
            ))
        elif category == "verification_failure" and finding.get("verification_status") == "failed":
            created.append(_inference("verification_failed", "high", "verified", finding.get("claim") or "Required verification failed.", [], refs))
        elif category in {"security_risk", "policy_violation", "dependency_risk"} and finding.get("finding_id") in blocking:
            kind = "security_attention_required" if category == "security_risk" else "policy_violation"
            created.append(_inference(kind, finding.get("severity") or "high", "verified", finding.get("claim") or finding.get("title") or "A project rule matched.", [], refs))
        elif category == "test_gap":
            created.append(_inference("test_attention_required", "medium", finding.get("evidence_strength") or "moderate", finding.get("claim") or "Production code changed without a related test update.", [], refs))
        elif origin == "temporal_only":
            created.append(_inference(
                "historical_association_only",
                "info",
                "weak",
                "This file has historical association with the change, but no current structural dependency has been confirmed.",
                [service] if service else [],
                refs,
            ))
        elif origin == "both" and service and service != root:
            created.append(_inference(
                "structural_and_historical_support",
                "low",
                "strong",
                "This file is structurally reachable from the change and historical evidence also supports follow-up.",
                [service],
                refs,
            ))
            created.append(_inference("cross_repository_review_required", "low", "moderate", f"{service} is a downstream review target.", [service], refs))
        elif origin == "structural_only" and service and service != root:
            created.append(_inference("structural_dependency_confirmed", "low", "moderate", f"{service} is structurally reachable from this change.", [service], refs))
            created.append(_inference("cross_repository_review_required", "low", "moderate", f"{service} is a downstream review target.", [service], refs))
        elif category == "review_target" and service and service != root:
            created.append(_inference("cross_repository_review_required", "low", "moderate", f"{service} is a downstream review target.", [service], refs))
    for contract in report.get("contract_changes") or []:
        if contract.get("breaking") and not contract.get("consumers"):
            created.append(_inference(
                "consumer_compatibility_review",
                "low",
                "moderate",
                contract.get("message") or "A contract changed and no known consumer was confirmed.",
                [],
                [],
            ))
    alignment = (report.get("change") or {}).get("graph_alignment")
    if alignment == "stale":
        created.append(_inference("stale_evidence", "low", "moderate", "The published graph does not match this revision. This is not a defect in the change.", [], []))
    if (report.get("gate") or {}).get("decision") == "incomplete":
        created.append(_inference("incomplete_evidence", "medium", "moderate", _incomplete_reason(report), [], []))
    if enforcement_mode == "deployment_gate" and (report.get("gate") or {}).get("decision") in {"warn", "block"}:
        created.append(_inference("deployment_attention_required", "medium", "moderate", "Deployment enforcement would treat this gate decision as something to resolve before promotion.", services, []))
    if any(item.get("source") == "LLM" for item in report.get("findings") or []):
        created.append(_inference("verification_required", "info", "weak", "A model suggestion is advisory until deterministic evidence supports it.", [], []))
    unique = _dedupe_inferences(created)
    for index, item in enumerate(unique, start=1):
        item["inference_id"] = f"INF-{index:03d}"
        item["recommended_actions"] = _actions_for_inference(item)
    return unique


def build_actions(report: dict[str, Any], inferences: list[dict[str, Any]]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for item in inferences:
        actions.extend(item.get("recommended_actions") or [])
    decision = (report.get("gate") or {}).get("decision")
    if decision == "incomplete" and not any(item.get("required") for item in actions):
        actions.append(_action("Refresh the evidence and run the review again", _incomplete_recovery(report), True, "A required check could not be completed.", ["BG-GRAPH-001", "BG-EVIDENCE-001"]))
    if decision == "pass" and not actions:
        actions.append(_action("No required change", "Required checks that ran were satisfied.", False, "No verified blocker was found.", []))
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in actions:
        if item["title"] in seen:
            continue
        seen.add(item["title"])
        deduped.append(item)
    deduped.sort(key=lambda item: (not item["required"], item["title"]))
    for index, item in enumerate(deduped, start=1):
        item["action_id"] = f"ACT-{index:03d}"
    by_title = {item["title"]: item["action_id"] for item in deduped}
    for inference in inferences:
        linked = []
        for action in inference.get("recommended_actions") or []:
            action_id = by_title.get(action["title"])
            if action_id:
                linked.append({**action, "action_id": action_id})
        inference["recommended_actions"] = linked
    return deduped


def build_decision_rules(report: dict[str, Any], inferences: list[dict[str, Any]], evidence: list[dict[str, Any]], actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    gate = report.get("gate") or {}
    decision = gate.get("decision") or "incomplete"
    codes = set(gate.get("reason_codes") or [])
    blocking = set(gate.get("blocking_findings") or [])
    triggered: list[str] = []
    by_inference = {item["type"]: item for item in inferences}
    if decision == "incomplete":
        if "graph_stale" in codes or "graph_missing" in codes or (report.get("change") or {}).get("graph_alignment") in {"stale", "unavailable"}:
            triggered.append("BG-GRAPH-001")
        if "verification_unavailable" in codes or "evidence_incomplete" in codes or "cancelled" in codes:
            triggered.append("BG-EVIDENCE-001")
        if not triggered:
            triggered.append("BG-EVIDENCE-001")
    for finding in report.get("findings") or []:
        if finding.get("finding_id") not in blocking:
            continue
        category = finding.get("category")
        if category == "contract_break":
            triggered.append("BG-CONTRACT-001")
        elif category == "verification_failure":
            triggered.append("BG-VERIFY-001")
        elif finding.get("severity") == "critical":
            triggered.append("BG-CRITICAL-001")
            if finding.get("policy_id"):
                triggered.append("BG-POLICY-001")
        elif category in {"policy_violation", "security_risk", "dependency_risk"}:
            triggered.append("BG-POLICY-001")
    if any(item["type"] == "historical_association_only" for item in inferences):
        triggered.append("BG-TEMPORAL-001")
    if any(item.get("source") == "LLM" for item in report.get("findings") or []):
        triggered.append("BG-AI-001")
    if decision == "warn" and any(item["type"] in {"cross_repository_review_required", "structural_dependency_confirmed", "structural_and_historical_support"} for item in inferences):
        triggered.append("BG-IMPACT-001")
    rules = []
    seen: set[str] = set()
    required_ids = [item["action_id"] for item in actions if item.get("required")]
    for rule_id in triggered:
        if rule_id in seen or rule_id not in GATE_RULES:
            continue
        seen.add(rule_id)
        meta = GATE_RULES[rule_id]
        related = _rule_inferences(rule_id, by_inference)
        evidence_refs: list[str] = []
        for item in related:
            evidence_refs.extend(item.get("evidence_refs") or [])
        if rule_id == "BG-GRAPH-001":
            evidence_refs.extend(item["evidence_id"] for item in evidence if item.get("kind") == "graph_alignment")
        grade = related[0]["confidence"] if related else ("moderate" if decision == "incomplete" else "verified")
        blocks = meta["decision"] == "block" and decision == "block"
        rules.append({
            "rule_id": rule_id,
            "title": meta["title"],
            "outcome": "triggered",
            "decision": meta["decision"] if not (rule_id == "BG-TEMPORAL-001") else "warn",
            "inference_refs": [item["inference_id"] for item in related],
            "evidence_refs": list(dict.fromkeys(evidence_refs)),
            "evidence_grade": grade,
            "required_actions": required_ids if blocks or (decision == "incomplete" and rule_id in {"BG-GRAPH-001", "BG-EVIDENCE-001"}) else [],
            "blocks": blocks,
        })
    return rules


def build_checklist(report: dict[str, Any], evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    change = report.get("change") or {}
    alignment = change.get("graph_alignment")
    graph_refs = [item["evidence_id"] for item in evidence if item.get("kind") == "graph_alignment"]
    if alignment in {"exact", "compatible"}:
        graph_status = "passed"
    elif alignment == "stale":
        graph_status = "warning"
    elif alignment == "unavailable":
        graph_status = "unavailable"
    else:
        graph_status = "not_evaluated"
    contracts = report.get("contract_changes")
    blocking = set((report.get("gate") or {}).get("blocking_findings") or [])
    contract_failed = any(item.get("category") == "contract_break" and item.get("finding_id") in blocking for item in report.get("findings") or [])
    contract_concern = isinstance(contracts, list) and any(item.get("breaking") and item.get("consumers") for item in contracts)
    contract_refs = [item["evidence_id"] for item in evidence if item.get("kind") in {"contract", "contract_removed"} or "contract" in str(item.get("kind"))]
    if contracts is None:
        contract_status = "not_evaluated"
    elif contract_failed:
        contract_status = "failed"
    elif contract_concern:
        contract_status = "warning"
    else:
        contract_status = "passed"
    verification = report.get("verification") or []
    verify_refs = [item["evidence_id"] for item in evidence if item.get("source") == "verification_command"]
    if not verification:
        verify_status = "not_evaluated"
    elif any(item.get("required") and item.get("status") in {"timeout", "skipped"} for item in verification):
        verify_status = "unavailable"
    elif any(item.get("required") and item.get("status") == "failed" for item in verification):
        verify_status = "failed"
    elif any(item.get("status") == "passed" for item in verification):
        verify_status = "passed"
    else:
        verify_status = "not_evaluated"
    policies = report.get("policies")
    policy_failed = any(item.get("category") in {"policy_violation", "security_risk", "dependency_risk"} and item.get("finding_id") in blocking for item in report.get("findings") or [])
    if policies is None:
        policy_status = "not_evaluated"
    elif policy_failed:
        policy_status = "failed"
    elif policies:
        policy_status = "passed" if not any(item.get("result") == "violation" and item.get("blocking") for item in policies) else "warning"
    else:
        policy_status = "not_evaluated"
    return [
        {"check_id": "CHECK-GRAPH", "label": "Dependency evidence current", "status": graph_status, "evidence_refs": graph_refs},
        {"check_id": "CHECK-CONTRACT", "label": "Contract compatibility", "status": contract_status, "evidence_refs": contract_refs},
        {"check_id": "CHECK-VERIFY", "label": "Required verification", "status": verify_status, "evidence_refs": verify_refs},
        {"check_id": "CHECK-POLICY", "label": "Project rules", "status": policy_status, "evidence_refs": []},
    ]


def build_assessment(report: dict[str, Any], inferences: list[dict[str, Any]], actions: list[dict[str, Any]]) -> dict[str, Any]:
    decision = (report.get("gate") or {}).get("decision") or "incomplete"
    change = report.get("change") or {}
    root = change.get("service_id") or "this service"
    primary = inferences[0] if inferences else None
    services = []
    for item in inferences:
        services.extend(item.get("affected_services") or [])
    services = list(dict.fromkeys(services))
    if decision == "block" and any(item["type"] == "consumer_compatibility_break" for item in inferences):
        consumer = next((name for item in inferences if item["type"] == "consumer_compatibility_break" for name in item["affected_services"]), "a known consumer")
        headline = "Known consumer compatibility is affected."
        summary = f"The reviewed change removes a contract consumed by {consumer}."
        level = "cross_service"
    elif decision == "block" and any(item["type"] == "verification_failed" for item in inferences):
        headline = "Required verification failed."
        summary = "A required verification command finished with a non-zero exit code."
        level = "service"
    elif decision == "block":
        headline = "Action is required before this change proceeds."
        summary = primary["statement"] if primary else "A verified project rule matched this change."
        level = "cross_service" if services else "service"
    elif decision == "incomplete":
        headline = "More evidence is needed."
        summary = _incomplete_reason(report)
        level = "service"
    elif decision == "warn" and services:
        headline = "Downstream review is recommended."
        structural = any(item["type"] in {"structural_dependency_confirmed", "structural_and_historical_support", "cross_repository_review_required"} for item in inferences)
        historical = any(item["type"] == "historical_association_only" for item in inferences)
        if structural and not any(item["type"] == "consumer_compatibility_break" for item in inferences):
            summary = f"This change can reach {services[0]} through current dependency evidence. No verified breaking contract was found."
        elif historical and not structural:
            summary = f"{services[0]} is historically associated with this change. A current structural dependency was not confirmed."
        else:
            summary = f"Review work remains for {', '.join(services[:3])}."
        level = "cross_service"
    elif decision == "warn":
        headline = "Review is recommended."
        summary = primary["statement"] if primary else f"The change in {root} has review work and no verified blocker."
        level = "service"
    else:
        headline = "This change is ready to proceed."
        summary = f"No verified blocker was found for {root}."
        level = "none"
    required = next((item for item in actions if item.get("required")), None)
    recommended = next((item for item in actions if not item.get("required")), None)
    next_step = (required or recommended or {}).get("description") or "No further action is required."
    return {
        "headline": headline,
        "summary": summary,
        "impact_level": level,
        "recommended_next_step": next_step,
        "inference_refs": [item["inference_id"] for item in inferences],
    }


def suppressed_findings(report: dict[str, Any], counter: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for item in report.get("diagnostics") or []:
        if item.get("code") != "evidence_rejected" and not item.get("suppressed"):
            continue
        rows.append({
            "potential_claim": item.get("claim") or item.get("category") or "Potential claim",
            "reason": item.get("message") or "The evidence did not support the claim.",
            "finding_id": item.get("finding_id"),
            "counter_evidence_refs": [entry["evidence_id"] for entry in counter if entry.get("finding_id") == item.get("finding_id")],
        })
    return rows


def copy_summary(report: dict[str, Any]) -> str:
    gate = report.get("gate") or {}
    decision = str(gate.get("decision") or "incomplete").upper()
    assessment = report.get("assessment") or {}
    summary = assessment.get("summary") or ""
    step = assessment.get("recommended_next_step") or ""
    review_id = report.get("review_id") or ""
    return f"Nami Trace: {decision}\n\n{summary}\n\nNext step: {step}\n\nReview ID: {review_id}"


def summarize_impact(result: dict[str, Any]) -> dict[str, str]:
    """Deterministic narrative for Impact Explorer. This is not a gate decision."""
    root = ((result.get("root") or {}).get("service_id")) or ""
    candidates = result.get("candidates") or result.get("ranked_candidates") or []
    services = []
    origins = set()
    top = None
    for item in candidates:
        service = item.get("service_id") or ""
        if service and service != root and service not in services:
            services.append(service)
        origin = item.get("evidence_origin") or ""
        origins.add(origin)
        if top is None:
            top = item
    if not candidates:
        return {
            "title": "No downstream review target",
            "statement": "Nami Rank did not find a downstream file for this root.",
            "recommended": "Choose another file or contract if you expected a dependency.",
        }
    structural = bool(origins & {"structural_only", "both"})
    target = services[0] if services else (top or {}).get("service_id") or "a downstream file"
    file_path = (top or {}).get("file_path") or ""
    if "both" in origins:
        title = "Cross-service impact found"
        statement = f"This change reaches {len(services) or 1} downstream service{'s' if len(services) != 1 else ''}. The strongest candidate is supported by both structural and historical evidence."
    elif "temporal_only" in origins and not structural:
        title = "Historically associated review target"
        statement = f"{target} has historical association with this change. A current structural dependency was not confirmed."
    else:
        title = "Downstream review target"
        statement = f"{target} is a downstream review target. This does not by itself mean a contract is broken."
    recommended = f"Review {file_path} before deployment." if file_path else f"Review {target} before deployment."
    return {"title": title, "statement": statement, "recommended": recommended}


def _inference(kind: str, severity: str, confidence: str, statement: str, services: list[str], refs: list[str]) -> dict[str, Any]:
    return {
        "type": kind,
        "title": INFERENCE_TITLES.get(kind, kind),
        "statement": statement,
        "severity": severity,
        "confidence": confidence,
        "evidence_refs": refs,
        "counter_evidence_refs": [],
        "affected_services": [item for item in services if item],
        "recommended_actions": [],
    }


def _actions_for_inference(item: dict[str, Any]) -> list[dict[str, Any]]:
    kind = item["type"]
    services = item.get("affected_services") or []
    consumer = services[0] if services else "the affected consumer"
    if kind == "consumer_compatibility_break":
        return [
            _action("Restore backward-compatible API", f"Restore the removed contract or keep a compatible operation for {consumer}.", True, "A known consumer uses the removed contract.", ["BG-CONTRACT-001"], "compatibility_resolution"),
            _action(f"Update {consumer} in the same release", f"Change {consumer} in the same release before this provider change is deployed.", True, "A known consumer uses the removed contract.", ["BG-CONTRACT-001"], "compatibility_resolution"),
        ]
    if kind == "verification_failed":
        return [_action("Fix the failing verification and rerun the review", "Correct the failure, then run Nami review again.", True, "A required verification command failed.", ["BG-VERIFY-001"])]
    if kind in {"policy_violation", "security_attention_required"}:
        text = item["statement"].lower()
        if "restricted" in text or "path" in text:
            title = "Move the change or update the approved policy"
            description = "Move the change outside the restricted path, or change the approved policy."
        else:
            title = "Remove the configured forbidden marker"
            description = "Remove the configured forbidden credential marker from the change."
        return [_action(title, description, True, item["statement"], ["BG-POLICY-001", "BG-CRITICAL-001"])]
    if kind == "incomplete_evidence":
        return [_action("Refresh intelligence or run Exact review", "The published graph does not match this revision. Refresh intelligence or run Exact review.", True, item["statement"], ["BG-GRAPH-001"])]
    if kind == "stale_evidence":
        return [_action("Refresh intelligence or run Exact review", "Refresh intelligence or run Exact review before relying on structural paths.", False, item["statement"], ["BG-GRAPH-001"])]
    if kind in {"cross_repository_review_required", "structural_dependency_confirmed", "structural_and_historical_support"}:
        target = services[0] if services else "the downstream service"
        return [_action(f"Review {target}", f"Read the downstream change in {target} before deployment.", False, item["statement"], ["BG-IMPACT-001"])]
    if kind == "historical_association_only":
        target = services[0] if services else "the associated file"
        return [_action(f"Review historical context for {target}", "Treat this as a review target. Historical association is not a confirmed dependency.", False, item["statement"], ["BG-TEMPORAL-001"])]
    if kind == "test_attention_required":
        return [_action("Review test coverage for the change", "Update or run the tests that should cover the changed production code.", False, item["statement"], [])]
    return []


def _action(title: str, description: str, required: bool, reason: str, satisfies: list[str], group: str = "") -> dict[str, Any]:
    body = {"title": title, "description": description, "required": required, "reason": reason, "satisfies": satisfies}
    if group:
        body["alternative_group"] = group
    return body


def _evidence_from_row(row: dict[str, Any], finding: dict[str, Any], change: dict[str, Any]) -> dict[str, Any]:
    source = row.get("source") or finding.get("source") or "diff"
    statement = _plain_statement(str(row.get("statement") or row.get("summary") or finding.get("claim") or ""))
    return {
        "kind": row.get("kind") or finding.get("category") or "observation",
        "source": source,
        "source_label": SOURCE_LABELS.get(source, source),
        "statement": statement,
        "service_id": row.get("service_id") or finding.get("service_id") or change.get("service_id"),
        "repository": row.get("repository") or finding.get("repository") or change.get("repository"),
        "file_path": row.get("file_path") if row.get("file_path") is not None else finding.get("file_path"),
        "line_start": row.get("line_start") if row.get("line_start") is not None else finding.get("line_start"),
        "line_end": row.get("line_end") if "line_end" in row else finding.get("line_end"),
        "base_sha": row.get("base_sha") or change.get("base_sha"),
        "head_sha": row.get("head_sha") or change.get("head_sha"),
        "graph_version": row.get("graph_version") or change.get("graph_version"),
        "freshness": "stale" if row.get("stale") else "current",
        "supports": [finding.get("category") or "finding"],
        "contradicts": [],
        "structured_data": row.get("structured_data") if isinstance(row.get("structured_data"), dict) else {},
        "provenance": {"extractor": source, "observed_at": None},
        "finding_id": finding.get("finding_id") or "",
    }


def _verification_evidence(item: dict[str, Any], change: dict[str, Any]) -> dict[str, Any]:
    status = item.get("status")
    statement = f"Verification {item.get('id')} {status}."
    if item.get("exit_code") is not None:
        statement = f"{statement} Exit code {item.get('exit_code')}."
    return {
        "kind": "verification_failed" if status == "failed" else "verification_executed",
        "source": "verification_command",
        "source_label": SOURCE_LABELS["verification_command"],
        "statement": statement,
        "service_id": change.get("service_id"),
        "repository": change.get("repository"),
        "file_path": None,
        "line_start": None,
        "line_end": None,
        "base_sha": change.get("base_sha"),
        "head_sha": change.get("head_sha"),
        "graph_version": change.get("graph_version"),
        "freshness": "current",
        "supports": ["verification_failed" if status == "failed" else "gate_check"],
        "contradicts": [],
        "structured_data": {"id": item.get("id"), "exit_code": item.get("exit_code"), "command": item.get("command"), "cwd": item.get("cwd")},
        "provenance": {"extractor": "verification_command", "observed_at": None},
        "finding_id": "",
    }


def _assign_ids(rows: list[dict[str, Any]], prefix: str, start: int = 1) -> list[dict[str, Any]]:
    ordered = sorted(rows, key=lambda item: (str(item.get("kind")), str(item.get("statement")), str(item.get("file_path")), str(item.get("line_start")), str(item.get("source"))))
    numbered = []
    for offset, item in enumerate(ordered):
        numbered.append({**item, "evidence_id": f"{prefix}-{start + offset:03d}"})
    return numbered


def _index_evidence(evidence: list[dict[str, Any]]) -> dict[str, list[str]]:
    index: dict[str, list[str]] = {}
    for item in evidence:
        finding_id = item.get("finding_id") or ""
        if not finding_id:
            continue
        index.setdefault(finding_id, []).append(item["evidence_id"])
    return index


def _consumer_name(finding: dict[str, Any]) -> str:
    structured = finding.get("structured") or {}
    consumers = list(structured.get("consumers") or [])
    contract = structured.get("contract") if isinstance(structured.get("contract"), dict) else {}
    consumers.extend(contract.get("consumers") or [])
    for row in finding.get("evidence") or []:
        data = row.get("structured_data") if isinstance(row.get("structured_data"), dict) else {}
        consumers.extend(data.get("consumers") or [])
        nested = data.get("contract")
        if isinstance(nested, dict):
            consumers.extend(nested.get("consumers") or [])
    for item in consumers:
        if isinstance(item, dict) and item.get("service_id"):
            return str(item["service_id"])
    return ""


def _plain_statement(statement: str) -> str:
    text = statement.strip()
    for prefix, ending in (("operation_removed ", "was removed."), ("operation_added ", "was added.")):
        if text.startswith(prefix):
            return f"{text[len(prefix):]} {ending}"
    return statement


def _alignment_statement(alignment: str) -> str:
    label = ALIGNMENT_LABELS.get(alignment, alignment)
    if alignment == "exact":
        return "The published graph matches this revision."
    if alignment == "compatible":
        return "The published graph is current enough for this revision."
    if alignment == "stale":
        return "The published graph is out of date for this revision."
    if alignment == "unavailable":
        return "Published graph evidence is unavailable for this revision."
    return label


def _incomplete_reason(report: dict[str, Any]) -> str:
    codes = set((report.get("gate") or {}).get("reason_codes") or [])
    alignment = (report.get("change") or {}).get("graph_alignment")
    if "cancelled" in codes:
        return "The review was cancelled before the gate could finish."
    if alignment == "stale" or "graph_stale" in codes:
        return "Nami could not confirm current structural dependencies because the published graph does not match this revision."
    if alignment == "unavailable" or "graph_missing" in codes:
        return "Nami could not confirm current structural dependencies because graph evidence is unavailable."
    if "verification_unavailable" in codes:
        return "A required verification command did not finish, so the gate cannot complete."
    messages = [item.get("message") for item in report.get("diagnostics") or [] if item.get("severity") == "error" and item.get("message")]
    if messages:
        return str(messages[0])
    return "Required evidence for this decision is missing."


def _incomplete_recovery(report: dict[str, Any]) -> str:
    codes = set((report.get("gate") or {}).get("reason_codes") or [])
    if "verification_unavailable" in codes:
        return "Run the required verification command, then review the change again."
    if "cancelled" in codes:
        return "Start the review again."
    return "Refresh intelligence or run Exact review."


def _rule_inferences(rule_id: str, by_type: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    mapping = {
        "BG-CONTRACT-001": ["consumer_compatibility_break"],
        "BG-VERIFY-001": ["verification_failed"],
        "BG-POLICY-001": ["policy_violation", "security_attention_required"],
        "BG-CRITICAL-001": ["security_attention_required", "policy_violation"],
        "BG-GRAPH-001": ["stale_evidence", "incomplete_evidence"],
        "BG-EVIDENCE-001": ["incomplete_evidence"],
        "BG-TEMPORAL-001": ["historical_association_only"],
        "BG-AI-001": ["verification_required"],
        "BG-IMPACT-001": ["cross_repository_review_required", "structural_and_historical_support", "structural_dependency_confirmed"],
    }
    return [by_type[kind] for kind in mapping.get(rule_id, []) if kind in by_type]


def _dedupe_inferences(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    unique = []
    for item in items:
        key = (item["type"], item["statement"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    unique.sort(key=lambda item: (item["type"], item["statement"]))
    return unique
