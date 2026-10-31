"""Independent check of a stored Boundary Gate decision.

This module replays the formal gate conditions from stored review artifacts.
It does not call the gate implementation, and it does not change the decision.
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

from dep_intel.review_gate_rules import (
    FATAL_DIAGNOSTIC_CODES,
    GATE_RULE_VALIDATION,
    INVARIANT_SPECS,
    MAX_COUNTERFACTUALS_PER_RULE,
    REQUIREMENT_LABELS,
    SOFT_EVIDENCE,
    SOURCE_CATEGORY,
    VALIDATOR_VERSION,
)
from dep_intel.review_sufficiency import GRADE_ORDER

_GRADE_RANK = GRADE_ORDER


def attach_gate_validation(report: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Add gate_validation. Decision, reason codes, blockers, and grades stay as stored."""
    before = _decision_snapshot(report)
    validation = validate_gate(report, config)
    after = _decision_snapshot(report)
    if before != after:
        raise RuntimeError("Gate validation changed the stored decision.")
    body = dict(report)
    body["gate_validation"] = validation
    return body


def validate_gate(report: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]:
    settings = gate_settings(report, config)
    observed = str((report.get("gate") or {}).get("decision") or "incomplete")
    expected = replay_gate_decision(report, settings)
    observation = observe(report, settings)
    rules = [_rule_record(rule, observation) for rule in _triggered_rules(report)]
    integrity = _reference_integrity(report)
    provenance = _provenance(report, rules, observation)
    invariants = _invariants(report, settings, observation, rules)
    counterfactuals = _counterfactuals(rules, observation)
    limitations = list(provenance["limitations"])
    failures: list[str] = []
    if observed != expected:
        failures.append("decision_mismatch")
    if not integrity["ok"]:
        failures.append("reference_integrity")
    if provenance["failures"]:
        failures.append("provenance")
    if invariants["failed"]:
        failures.append("invariant")
    if counterfactuals["failed"]:
        failures.append("counterfactual")
    blocking_rules = [item for item in rules if _spec(item["rule_id"])["decision"] == "block"]
    if any(item["status"] == "failed" for item in blocking_rules):
        failures.append("rule_proof")
    if any(item["status"] == "failed" for item in rules if _spec(item["rule_id"])["decision"] != "block"):
        failures.append("rule_proof")
    if observed == "block" and not any(item["status"] == "validated" for item in blocking_rules):
        failures.append("block_without_validated_rule")
    if failures:
        status = "failed"
    elif limitations:
        status = "validated_with_limitations"
    else:
        status = "validated"
    fingerprint = gate_input_hash(report, settings)
    change = report.get("change") or {}
    return {
        "status": status,
        "summary": _summary(status, observed, expected),
        "validator_version": VALIDATOR_VERSION,
        "observed_decision": observed,
        "expected_decision": expected,
        "decision_match": observed == expected,
        "rule_validation": rules,
        "evidence_validation": {
            "reference_integrity": integrity,
            "provenance": {"failures": provenance["failures"], "checked": provenance["checked"]},
            "categories": {
                "hard": sorted(name for name, kind in SOURCE_CATEGORY.items() if kind == "hard"),
                "supporting": sorted(name for name, kind in SOURCE_CATEGORY.items() if kind == "supporting"),
                "advisory": sorted(name for name, kind in SOURCE_CATEGORY.items() if kind == "advisory"),
            },
        },
        "invariants": invariants,
        "counterfactuals": counterfactuals,
        "reproducibility": {
            "gate_input_hash": fingerprint,
            "validator_version": VALIDATOR_VERSION,
            "graph_version": change.get("graph_version") or "",
            "base_sha": change.get("base_sha") or "",
            "head_sha": change.get("head_sha") or "",
            "note": "Integrity fingerprint, not digital signature.",
        },
        "gate_configuration": settings,
        "limitations": limitations,
        "failures": failures,
    }


def replay_gate_decision(report: dict[str, Any], settings: dict[str, Any] | None = None) -> str:
    """Replay I, then B, then W from stored artifacts.

    D = INCOMPLETE if I; BLOCK if not I and B; WARN if not I and not B and W; PASS otherwise.
    priority_score is not read.
    """
    chosen = settings if settings is not None else gate_settings(report, None)
    findings = list(report.get("findings") or [])
    verification = list(report.get("verification") or [])
    diagnostics = list(report.get("diagnostics") or [])
    alignment = str((report.get("change") or {}).get("graph_alignment") or "")
    control = report.get("review_control") or {}
    reason_codes = set((report.get("gate") or {}).get("reason_codes") or [])
    if control.get("pre_gate_abort") or control.get("cancelled") or "cancelled" in reason_codes:
        return "incomplete"
    fatal = [item for item in diagnostics if item.get("severity") == "error" and item.get("code") in FATAL_DIAGNOSTIC_CODES]
    unavailable_required = [item for item in verification if item.get("required") and item.get("status") in {"timeout", "skipped"}]
    evidence_complete = True
    if chosen.get("strict_graph") and alignment in {"stale", "unavailable"}:
        evidence_complete = False
    if chosen.get("incomplete_on_required_verification_failure", True) and unavailable_required:
        evidence_complete = False
    if fatal or (not evidence_complete and (chosen.get("strict_graph") or unavailable_required)):
        return "incomplete"
    blocking = [item for item in findings if finding_is_verified_blocker(item, chosen)]
    if blocking:
        return "block"
    if any(not finding_is_verified_blocker(item, chosen) for item in findings) or alignment == "stale":
        return "warn"
    return "pass"


def finding_is_verified_blocker(finding: dict[str, Any], settings: dict[str, Any]) -> bool:
    """Stored-finding predicate for a verified blocker. Ranking scores are ignored."""
    origin = (finding.get("structured") or {}).get("evidence_origin")
    if origin == "temporal_only" or finding.get("category") == "review_target":
        return False
    if finding.get("source") == "LLM":
        return False
    if finding.get("evidence_strength") != "verified":
        return False
    if finding.get("severity") == "critical" and settings.get("block_on_verified_critical", True):
        return True
    if finding.get("blocking") and finding.get("severity") in {"critical", "high"} and settings.get("block_on_verified_high_blocker_policy", True):
        return True
    if finding.get("category") == "contract_break" and settings.get("block_on_breaking_contract_with_known_consumers", True):
        return True
    if finding.get("category") == "verification_failure" and finding.get("verification_status") == "failed":
        return True
    return False


def gate_settings(report: dict[str, Any], config: dict[str, Any] | None) -> dict[str, Any]:
    raw: dict[str, Any] = {}
    if isinstance(config, dict) and isinstance(config.get("gate"), dict):
        raw = config["gate"]
    elif isinstance((report.get("gate_validation") or {}).get("gate_configuration"), dict):
        raw = report["gate_validation"]["gate_configuration"]
    return {
        "strict_graph": bool(raw.get("strict_graph", False)),
        "incomplete_on_required_verification_failure": raw.get("incomplete_on_required_verification_failure", True) is not False,
        "block_on_verified_critical": raw.get("block_on_verified_critical", True) is not False,
        "block_on_verified_high_blocker_policy": raw.get("block_on_verified_high_blocker_policy", True) is not False,
        "block_on_breaking_contract_with_known_consumers": raw.get("block_on_breaking_contract_with_known_consumers", True) is not False,
    }


def gate_input_hash(report: dict[str, Any], settings: dict[str, Any] | None = None) -> str:
    """Fingerprint of decision inputs. Commentary and presentation fields are excluded."""
    chosen = settings if settings is not None else gate_settings(report, None)
    change = report.get("change") or {}
    control = report.get("review_control") or {}
    findings = []
    for finding in report.get("findings") or []:
        findings.append({
            "finding_id": finding.get("finding_id"),
            "category": finding.get("category"),
            "severity": finding.get("severity"),
            "evidence_strength": finding.get("evidence_strength"),
            "source": finding.get("source"),
            "blocking": bool(finding.get("blocking")),
            "verification_status": finding.get("verification_status"),
            "evidence_origin": (finding.get("structured") or {}).get("evidence_origin"),
            "policy_id": finding.get("policy_id"),
            "consumers": sorted(_consumer_ids(finding.get("structured") or {}) + _consumer_ids_from_evidence(finding)),
        })
    findings.sort(key=lambda item: str(item.get("finding_id")))
    evidence = []
    for item in list(report.get("evidence_catalog") or []) + list(report.get("counter_evidence") or []):
        evidence.append({
            "evidence_id": item.get("evidence_id"),
            "kind": item.get("kind"),
            "source": item.get("source"),
            "freshness": item.get("freshness"),
            "supports": sorted(str(value) for value in item.get("supports") or []),
            "contradicts": sorted(str(value) for value in item.get("contradicts") or []),
            "finding_id": item.get("finding_id") or "",
        })
    evidence.sort(key=lambda item: str(item.get("evidence_id")))
    verification = []
    for item in report.get("verification") or []:
        verification.append({
            "id": item.get("id"),
            "status": item.get("status"),
            "required": bool(item.get("required")),
            "exit_code": item.get("exit_code"),
        })
    verification.sort(key=lambda item: str(item.get("id")))
    diagnostics = sorted(
        {"code": item.get("code"), "severity": item.get("severity")}
        for item in report.get("diagnostics") or []
        if item.get("code") in FATAL_DIAGNOSTIC_CODES or item.get("code") == "cancelled"
    )
    payload = {
        "alignment": change.get("graph_alignment"),
        "base_sha": change.get("base_sha"),
        "head_sha": change.get("head_sha"),
        "graph_version": change.get("graph_version"),
        "gate_configuration": chosen,
        "review_control": {"pre_gate_abort": bool(control.get("pre_gate_abort")), "cancelled": bool(control.get("cancelled"))},
        "findings": findings,
        "evidence": evidence,
        "verification": verification,
        "diagnostics": diagnostics,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def observe(report: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
    """Collect requirement facts from the stored catalogue. Does not invent missing fields."""
    requirements = {name: {"satisfied": False, "evidence_refs": [], "sources": []} for name in REQUIREMENT_LABELS}
    alignment = str((report.get("change") or {}).get("graph_alignment") or "")
    control = report.get("review_control") or {}
    reason_codes = set((report.get("gate") or {}).get("reason_codes") or [])
    catalog = list(report.get("evidence_catalog") or [])
    counters = list(report.get("counter_evidence") or [])
    for item in catalog:
        _absorb_evidence_item(requirements, item)
    for finding in report.get("findings") or []:
        _absorb_finding(requirements, finding)
    for item in report.get("verification") or []:
        _absorb_verification(requirements, item)
    for contract in report.get("contract_changes") or []:
        _absorb_contract(requirements, contract)
    if alignment in {"stale", "unavailable"} or reason_codes & {"graph_stale", "graph_missing"}:
        _satisfy(requirements, "graph_not_current", _ids_for_kind(catalog, "graph_alignment"), ["cluster_graph"])
    if (
        control.get("pre_gate_abort")
        or control.get("cancelled")
        or "cancelled" in reason_codes
        or reason_codes & {"verification_unavailable", "evidence_incomplete"}
        or any(item.get("required") and item.get("status") in {"timeout", "skipped"} for item in report.get("verification") or [])
    ):
        _satisfy(requirements, "required_evidence_unavailable", [], [])
    structural_ids = [item.get("evidence_id") for item in catalog if item.get("kind") == "graph_alignment" and item.get("evidence_id")]
    contract_fresh = True
    for finding in report.get("findings") or []:
        if finding.get("category") != "contract_break":
            continue
        for row in finding.get("evidence") or []:
            if row.get("stale") or row.get("freshness") == "stale":
                contract_fresh = False
        for item in catalog:
            if item.get("finding_id") == finding.get("finding_id") and item.get("freshness") == "stale":
                contract_fresh = False
            if item.get("finding_id") == finding.get("finding_id") and item.get("evidence_id"):
                structural_ids.append(item["evidence_id"])
    if alignment in {"exact", "compatible"} and contract_fresh:
        _satisfy(requirements, "current_structural_evidence", structural_ids, ["cluster_graph"])
    grades: dict[str, str] = {}
    sources: dict[str, list[str]] = {}
    for rule_id in GATE_RULE_VALIDATION:
        matched = [item for item in report.get("findings") or [] if rule_id in _blocking_rule_ids(item, settings)]
        if matched:
            grades[rule_id] = str(matched[0].get("evidence_strength") or "")
            sources[rule_id] = sorted(_sources_of(matched[0]))
        else:
            grades[rule_id] = ""
            sources[rule_id] = []
    decisive = {rule_id: _decisive(report, counters, rule_id) for rule_id in GATE_RULE_VALIDATION}
    return {
        "alignment": alignment,
        "requirements": requirements,
        "grades": grades,
        "sources": sources,
        "decisive": decisive,
        "catalog_ids": {item.get("evidence_id") for item in catalog if item.get("evidence_id")},
    }


def rule_satisfied(rule_id: str, observation: dict[str, Any]) -> bool:
    spec = _spec(rule_id)
    if spec["decision"] == "block":
        grade = observation["grades"].get(rule_id) or ""
        if _GRADE_RANK.get(grade, 0) < _GRADE_RANK.get(spec.get("required_grade") or "verified", 4):
            return False
        sources = set(observation["sources"].get(rule_id) or [])
        if not sources or not any(SOURCE_CATEGORY.get(source) == "hard" for source in sources):
            return False
        if sources <= SOFT_EVIDENCE:
            return False
        if observation["decisive"].get(rule_id):
            return False
    names = list(spec.get("required_evidence") or [])
    if spec.get("requires_current_structural_evidence"):
        names.append("current_structural_evidence")
    for name in names:
        if not (observation["requirements"].get(name) or {}).get("satisfied"):
            return False
    return True


def _catalog_refs(refs: list[Any], catalog_ids: Any) -> list[str]:
    known = catalog_ids if isinstance(catalog_ids, set) else None
    kept: list[str] = []
    for ref in refs:
        if not ref or ref in kept:
            continue
        if known is not None and ref not in known:
            continue
        if known is None and not str(ref).startswith("EV-"):
            continue
        kept.append(str(ref))
    return kept


def _rule_record(rule: dict[str, Any], observation: dict[str, Any]) -> dict[str, Any]:
    rule_id = str(rule.get("rule_id") or "")
    spec = _spec(rule_id)
    names = list(spec.get("required_evidence") or [])
    if spec.get("requires_current_structural_evidence"):
        names.append("current_structural_evidence")
    requirements = []
    for name in names:
        fact = observation["requirements"].get(name) or {}
        requirements.append({
            "requirement": name,
            "label": REQUIREMENT_LABELS.get(name, name),
            "status": "satisfied" if fact.get("satisfied") else "missing",
            "evidence_refs": _catalog_refs(fact.get("evidence_refs") or [], observation.get("catalog_ids")),
        })
    satisfied = rule_id in GATE_RULE_VALIDATION and rule_satisfied(rule_id, observation)
    if spec["decision"] == "block" and observation["decisive"].get(rule_id):
        counter = "decisive_contradiction"
    elif spec["decision"] == "block":
        counter = "none_decisive"
    else:
        counter = "none_decisive"
    grade = observation["grades"].get(rule_id) or rule.get("evidence_grade") or ""
    return {
        "rule_id": rule_id,
        "title": spec.get("title") or rule.get("title") or rule_id,
        "expected_decision": spec.get("decision") or rule.get("decision"),
        "status": "validated" if satisfied else "failed",
        "requirements": requirements,
        "evidence_grade": grade,
        "counter_evidence_status": counter,
    }


def _counterfactuals(rules: list[dict[str, Any]], observation: dict[str, Any]) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for rule in rules:
        if rule.get("status") != "validated":
            continue
        rule_id = rule["rule_id"]
        spec = _spec(rule_id)
        cases: list[tuple[str, str, str]] = []
        if rule_id == "BG-CONTRACT-001":
            cases.append(("CF-CONTRACT-CONSUMER", "Remove known-consumer evidence", "drop_known_consumer"))
        if rule_id == "BG-VERIFY-001":
            cases.append(("CF-VERIFY-PASS", "Change the verification exit code to zero", "verification_passed"))
        if spec.get("decision") == "block" and spec.get("required_grade") == "verified":
            cases.append(("CF-GRADE", "Downgrade verified evidence to strong", "downgrade_grade"))
        if spec.get("requires_current_structural_evidence"):
            cases.append(("CF-FRESHNESS", "Mark structural evidence stale", "alignment_stale"))
        for case_id, change, mutation in cases[:MAX_COUNTERFACTUALS_PER_RULE]:
            mutated = copy.deepcopy(observation)
            _apply_mutation(mutated, rule_id, mutation)
            held = rule_satisfied(rule_id, mutated)
            observed = "rule_satisfied" if held else "rule_not_satisfied"
            items.append({
                "counterfactual_id": f"CF-{len(items) + 1:03d}",
                "case_id": case_id,
                "rule_id": rule_id,
                "change": change,
                "expected": "rule_not_satisfied",
                "observed": observed,
                "status": "passed" if observed == "rule_not_satisfied" else "failed",
            })
    passed = sum(1 for item in items if item["status"] == "passed")
    return {"total": len(items), "passed": passed, "failed": len(items) - passed, "items": items}


def _apply_mutation(observation: dict[str, Any], rule_id: str, mutation: str) -> None:
    if mutation == "drop_known_consumer":
        fact = observation["requirements"].setdefault("known_consumer", {"satisfied": False, "evidence_refs": [], "sources": []})
        fact["satisfied"] = False
        fact["evidence_refs"] = []
    elif mutation == "verification_passed":
        fact = observation["requirements"].setdefault("verification_failed", {"satisfied": False, "evidence_refs": [], "sources": []})
        fact["satisfied"] = False
        fact["evidence_refs"] = []
    elif mutation == "downgrade_grade":
        observation["grades"][rule_id] = "strong"
    elif mutation == "alignment_stale":
        observation["alignment"] = "stale"
        fact = observation["requirements"].setdefault("current_structural_evidence", {"satisfied": False, "evidence_refs": [], "sources": []})
        fact["satisfied"] = False


def _invariants(report: dict[str, Any], settings: dict[str, Any], observation: dict[str, Any], rules: list[dict[str, Any]]) -> dict[str, Any]:
    gate = report.get("gate") or {}
    blocking_ids = set(gate.get("blocking_findings") or [])
    blocking = [item for item in report.get("findings") or [] if item.get("finding_id") in blocking_ids]
    results = {item_id: "passed" for item_id, _title in INVARIANT_SPECS}

    def soft_only(finding: dict[str, Any]) -> bool:
        sources = _sources_of(finding)
        return bool(sources) and sources <= SOFT_EVIDENCE

    if any(soft_only(item) for item in blocking):
        results["INV-001"] = "failed"
    if any((item.get("structured") or {}).get("evidence_origin") == "temporal_only" for item in blocking):
        results["INV-002"] = "failed"
    if any(item.get("source") == "LLM" or _sources_of(item) <= {"LLM"} and _sources_of(item) for item in blocking):
        results["INV-003"] = "failed"
    if any(item.get("evidence_strength") != "verified" for item in blocking):
        results["INV-004"] = "failed"
    if any(_GRADE_RANK.get(item.get("evidence_grade") or "", 0) < _GRADE_RANK["verified"] for item in rules if _spec(item["rule_id"])["decision"] == "block" and item["status"] == "validated"):
        results["INV-004"] = "failed"
    contract_blocked = any(item.get("category") == "contract_break" for item in blocking) or any(item["rule_id"] == "BG-CONTRACT-001" and item["status"] == "validated" for item in rules)
    known = (observation["requirements"].get("known_consumer") or {}).get("satisfied")
    if contract_blocked and not known:
        results["INV-005"] = "failed"
    verify_validated = any(item["rule_id"] == "BG-VERIFY-001" and item["status"] == "validated" for item in rules)
    failed_commands = [item for item in report.get("verification") or [] if item.get("status") == "failed" and item.get("exit_code") not in (0, None)]
    passed_only = bool(report.get("verification")) and all(item.get("status") == "passed" or item.get("exit_code") == 0 for item in report.get("verification") or [])
    if verify_validated and (passed_only or not failed_commands):
        results["INV-006"] = "failed"
    if any(item.get("category") == "verification_failure" and item.get("verification_status") == "failed" for item in blocking) and passed_only:
        results["INV-006"] = "failed"
    for item in rules:
        spec = _spec(item["rule_id"])
        if item["status"] != "validated" or spec.get("decision") != "block" or not spec.get("requires_current_structural_evidence"):
            continue
        if observation.get("alignment") in {"stale", "unavailable"} or not (observation["requirements"].get("current_structural_evidence") or {}).get("satisfied"):
            results["INV-007"] = "failed"
    baseline = replay_gate_decision(report, settings)
    muted = copy.deepcopy(report)
    for finding in muted.get("findings") or []:
        finding["priority_score"] = 0
        structured = dict(finding.get("structured") or {})
        structured["hybrid_score"] = 0
        finding["structured"] = structured
    loud = copy.deepcopy(report)
    for finding in loud.get("findings") or []:
        finding["priority_score"] = 999
        structured = dict(finding.get("structured") or {})
        structured["hybrid_score"] = 1
        finding["structured"] = structured
    if replay_gate_decision(muted, settings) != baseline or replay_gate_decision(loud, settings) != baseline:
        results["INV-008"] = "failed"
    if any(item["status"] == "validated" and item.get("counter_evidence_status") == "decisive_contradiction" and _spec(item["rule_id"])["decision"] == "block" for item in rules):
        results["INV-009"] = "failed"
    if any(item.get("category") == "review_target" for item in blocking):
        results["INV-010"] = "failed"
    items = [{"invariant_id": item_id, "title": title, "status": results[item_id]} for item_id, title in INVARIANT_SPECS]
    failed = sum(1 for item in items if item["status"] == "failed")
    return {"total": len(items), "passed": len(items) - failed, "failed": failed, "items": items}


def _reference_integrity(report: dict[str, Any]) -> dict[str, Any]:
    evidence = list(report.get("evidence_catalog") or []) + list(report.get("counter_evidence") or [])
    evidence_ids = [item.get("evidence_id") for item in evidence if item.get("evidence_id")]
    inferences = list(report.get("inferences") or [])
    inference_ids = [item.get("inference_id") for item in inferences if item.get("inference_id")]
    action_ids = {item.get("action_id") for item in report.get("actions") or [] if item.get("action_id")}
    problems: list[str] = []
    if len(evidence_ids) != len(set(evidence_ids)):
        problems.append("duplicate evidence ids")
    if len(inference_ids) != len(set(inference_ids)):
        problems.append("duplicate inference ids")
    known_evidence = set(evidence_ids)
    known_inferences = set(inference_ids)
    for rule in (report.get("gate") or {}).get("decision_rules") or []:
        rule_id = rule.get("rule_id")
        if rule_id not in GATE_RULE_VALIDATION:
            problems.append(f"unknown rule {rule_id}")
        for ref in rule.get("evidence_refs") or []:
            if ref and ref not in known_evidence:
                problems.append(f"unresolved evidence {ref}")
        for ref in rule.get("inference_refs") or []:
            if ref and ref not in known_inferences:
                problems.append(f"unresolved inference {ref}")
        for ref in rule.get("required_actions") or []:
            if ref and ref not in action_ids:
                problems.append(f"unresolved action {ref}")
    for item in inferences:
        for ref in list(item.get("evidence_refs") or []) + list(item.get("counter_evidence_refs") or []):
            if ref and ref not in known_evidence:
                problems.append(f"unresolved evidence {ref}")
    for item in report.get("gate_checklist") or []:
        for ref in item.get("evidence_refs") or []:
            if ref and ref not in known_evidence:
                problems.append(f"unresolved evidence {ref}")
    for ref in (report.get("assessment") or {}).get("inference_refs") or []:
        if ref and ref not in known_inferences:
            problems.append(f"unresolved inference {ref}")
    unique = list(dict.fromkeys(problems))
    return {"ok": not unique, "problems": unique}


def _provenance(report: dict[str, Any], rules: list[dict[str, Any]], observation: dict[str, Any]) -> dict[str, Any]:
    failures: list[str] = []
    limitations: list[str] = []
    change = report.get("change") or {}
    used: set[str] = set()
    needs_structure = False
    for rule in rules:
        spec = _spec(rule["rule_id"])
        if spec.get("decision") != "block":
            continue
        if spec.get("requires_current_structural_evidence"):
            needs_structure = True
        for requirement in rule.get("requirements") or []:
            used.update(requirement.get("evidence_refs") or [])
    by_id = {item.get("evidence_id"): item for item in list(report.get("evidence_catalog") or []) + list(report.get("counter_evidence") or [])}
    checked = 0
    for evidence_id in used:
        item = by_id.get(evidence_id)
        if item is None:
            continue
        checked += 1
        if not item.get("source"):
            failures.append(f"{evidence_id} has no source")
        path = item.get("file_path")
        if path and not _path_safe(str(path)):
            failures.append(f"{evidence_id} has an unsafe file path")
        line = item.get("line_start")
        if line is not None and not _line_plausible(line):
            failures.append(f"{evidence_id} has an implausible line")
        if item.get("freshness") == "stale" and observation.get("alignment") in {"exact", "compatible"} and needs_structure:
            failures.append(f"{evidence_id} is stale while the rule requires current structural evidence")
    if needs_structure:
        if not change.get("graph_version"):
            limitations.append("Graph version was not recorded for a rule that requires current structural evidence.")
        if not change.get("base_sha"):
            limitations.append("Base revision was not recorded.")
        if not change.get("head_sha"):
            limitations.append("Head revision was not recorded.")
    return {"failures": list(dict.fromkeys(failures)), "limitations": list(dict.fromkeys(limitations)), "checked": checked}


def _summary(status: str, observed: str, expected: str) -> str:
    label = observed.upper()
    if status == "validated":
        return f"Nami independently replayed the Gate rules and reproduced the same {label} decision."
    if status == "validated_with_limitations":
        return "The Gate decision was reproduced, but one optional provenance check could not be completed."
    if observed != expected:
        return "Nami could not independently reproduce the stored Gate decision."
    return "Nami could not justify the stored Gate decision from the evidence requirements."


def _triggered_rules(report: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in (report.get("gate") or {}).get("decision_rules") or [] if item.get("outcome", "triggered") == "triggered"]


def _spec(rule_id: str) -> dict[str, Any]:
    return GATE_RULE_VALIDATION.get(rule_id) or {
        "title": rule_id,
        "decision": "",
        "required_grade": "",
        "required_evidence": [],
        "requires_current_structural_evidence": False,
        "decisive_counter_kinds": [],
    }


def _decision_snapshot(report: dict[str, Any]) -> str:
    gate = report.get("gate") or {}
    grades = [(item.get("finding_id"), item.get("evidence_strength")) for item in report.get("findings") or []]
    return json.dumps({
        "decision": gate.get("decision"),
        "reason_codes": gate.get("reason_codes"),
        "blocking_findings": gate.get("blocking_findings"),
        "grades": grades,
    }, sort_keys=True, default=str)


def _blocking_rule_ids(finding: dict[str, Any], settings: dict[str, Any]) -> list[str]:
    if not finding_is_verified_blocker(finding, settings):
        return []
    category = finding.get("category")
    rules: list[str] = []
    if category == "contract_break":
        rules.append("BG-CONTRACT-001")
    elif category == "verification_failure":
        rules.append("BG-VERIFY-001")
    elif finding.get("severity") == "critical":
        rules.append("BG-CRITICAL-001")
        if finding.get("policy_id"):
            rules.append("BG-POLICY-001")
    elif category in {"policy_violation", "security_risk", "dependency_risk"}:
        rules.append("BG-POLICY-001")
    return rules


def _absorb_evidence_item(requirements: dict[str, dict[str, Any]], item: dict[str, Any]) -> None:
    evidence_id = item.get("evidence_id")
    source = str(item.get("source") or "")
    kind = str(item.get("kind") or "")
    data = item.get("structured_data") if isinstance(item.get("structured_data"), dict) else {}
    blob = " ".join([kind, str(data.get("kind") or ""), str(item.get("statement") or ""), str(item.get("summary") or "")]).lower()
    refs = [evidence_id] if isinstance(evidence_id, str) and evidence_id.startswith("EV-") else []
    if kind in {"contract_removed", "operation_removed"} or str(data.get("kind") or "") in {"operation_removed", "contract_removed"} or ("removed" in blob and ("contract" in blob or "operation" in blob or "/api/" in blob)):
        _satisfy(requirements, "contract_removed", refs, [source] if source else [])
    if kind == "known_consumer" or _consumer_ids(data):
        _satisfy(requirements, "known_consumer", refs, [source] if source else [])
    if kind in {"verification_executed", "verification_failed"} or source == "verification_command":
        if kind in {"verification_executed", "verification_failed"} or "passed" in blob or "failed" in blob:
            _satisfy(requirements, "verification_executed", refs, [source] if source else [])
        failed_exit = "exit code" in blob and "exit code 0" not in blob and "failed" in blob
        if kind == "verification_failed" or failed_exit:
            _satisfy(requirements, "verification_failed", refs, [source] if source else [])
    if source == "policy" or kind in {"policy", "policy_match"}:
        _satisfy(requirements, "policy_match", refs, [source] if source else [])
        if item.get("file_path"):
            _satisfy(requirements, "changed_added_line", refs, [source] if source else [])
    if kind in {"changed_added_line"}:
        _satisfy(requirements, "changed_added_line", refs, [source] if source else [])
    if source == "git_history" or kind == "historical_association":
        _satisfy(requirements, "historical_association", refs, [source] if source else [])
    if source == "LLM" or kind in {"model", "model_suggestion"}:
        _satisfy(requirements, "model_suggestion", refs, [source] if source else [])
    origin = str(data.get("evidence_origin") or "")
    if origin in {"structural_only", "both"} or kind == "cross_repo_impact":
        _satisfy(requirements, "structural_path", refs, [source] if source else [])
    if origin == "temporal_only":
        _satisfy(requirements, "historical_association", refs, [source] if source else [])


def _absorb_finding(requirements: dict[str, dict[str, Any]], finding: dict[str, Any]) -> None:
    source = str(finding.get("source") or "")
    structured = finding.get("structured") if isinstance(finding.get("structured"), dict) else {}
    origin = str(structured.get("evidence_origin") or "")
    if finding.get("category") == "contract_break":
        _satisfy(requirements, "contract_removed", [], [source] if source else [])
    if _consumer_ids(structured) or _consumer_ids_from_evidence(finding):
        _satisfy(requirements, "known_consumer", [], [source] if source else [])
    if origin == "temporal_only" or source == "git_history":
        _satisfy(requirements, "historical_association", [], [source] if source else [])
    if origin in {"structural_only", "both"} or finding.get("category") == "cross_repo_impact":
        _satisfy(requirements, "structural_path", [], [source] if source else [])
    if source == "LLM":
        _satisfy(requirements, "model_suggestion", [], [source])
    if source == "policy" or finding.get("policy_id"):
        _satisfy(requirements, "policy_match", [], [source] if source else [])
        if finding.get("file_path"):
            _satisfy(requirements, "changed_added_line", [], [source] if source else [])
    if finding.get("category") == "verification_failure" and finding.get("verification_status") == "failed":
        _satisfy(requirements, "verification_executed", [], ["verification_command"])
        _satisfy(requirements, "verification_failed", [], ["verification_command"])
    for row in finding.get("evidence") or []:
        if isinstance(row, dict):
            _absorb_evidence_item(requirements, row)


def _absorb_verification(requirements: dict[str, dict[str, Any]], item: dict[str, Any]) -> None:
    status = item.get("status")
    if status in {"passed", "failed"}:
        _satisfy(requirements, "verification_executed", [], ["verification_command"])
    if status == "failed" and item.get("exit_code") not in (0, None):
        _satisfy(requirements, "verification_failed", [], ["verification_command"])
    elif status == "failed" and item.get("exit_code") is None:
        _satisfy(requirements, "verification_failed", [], ["verification_command"])


def _absorb_contract(requirements: dict[str, dict[str, Any]], contract: dict[str, Any]) -> None:
    kind = str(contract.get("kind") or "")
    if contract.get("breaking") or "removed" in kind:
        _satisfy(requirements, "contract_removed", [], ["diff"])
    if contract.get("consumers"):
        _satisfy(requirements, "known_consumer", [], ["cluster_graph"])


def _satisfy(requirements: dict[str, dict[str, Any]], name: str, refs: list[Any], sources: list[str]) -> None:
    fact = requirements.setdefault(name, {"satisfied": False, "evidence_refs": [], "sources": []})
    fact["satisfied"] = True
    for ref in refs:
        if ref and ref not in fact["evidence_refs"]:
            fact["evidence_refs"].append(ref)
    for source in sources:
        if source and source not in fact["sources"]:
            fact["sources"].append(source)


def _decisive(report: dict[str, Any], counters: list[dict[str, Any]], rule_id: str) -> bool:
    kinds = set(_spec(rule_id).get("decisive_counter_kinds") or [])
    claim = _spec(rule_id).get("required_claim_type")
    if not kinds and not claim:
        return False
    blocking_ids = set((report.get("gate") or {}).get("blocking_findings") or [])
    rows = list(counters)
    for finding in report.get("findings") or []:
        if finding.get("finding_id") not in blocking_ids:
            continue
        for extra in finding.get("counter_evidence") or []:
            if isinstance(extra, dict):
                rows.append({**extra, "finding_id": finding.get("finding_id")})
    for item in report.get("diagnostics") or []:
        if not (item.get("suppressed") or item.get("code") == "evidence_rejected"):
            continue
        if item.get("finding_id") and item.get("finding_id") not in blocking_ids:
            continue
        rows.append({
            "kind": item.get("kind"),
            "finding_id": item.get("finding_id"),
            "contradicts": [item.get("category")] if item.get("category") else [],
            "statement": item.get("message") or "",
            "category": item.get("category"),
        })
    for row in rows:
        kind = str(row.get("kind") or "")
        contradicts = set(row.get("contradicts") or [])
        if kind in kinds or (claim and claim in contradicts):
            return True
        statement = str(row.get("statement") or row.get("message") or "").lower()
        if rule_id == "BG-CONTRACT-001" and ("still defines" in statement or "still present" in statement):
            return True
    return False


def _sources_of(finding: dict[str, Any]) -> set[str]:
    sources = set()
    if finding.get("source"):
        sources.add(str(finding["source"]))
    for row in finding.get("evidence") or []:
        if isinstance(row, dict) and row.get("source"):
            sources.add(str(row["source"]))
    return sources


def _consumer_ids(data: Any) -> list[str]:
    if not isinstance(data, dict):
        return []
    found: list[str] = []
    for item in data.get("consumers") or []:
        if isinstance(item, dict) and item.get("service_id"):
            found.append(str(item["service_id"]))
        elif isinstance(item, str) and item:
            found.append(item)
    contract = data.get("contract")
    if isinstance(contract, dict):
        found.extend(_consumer_ids(contract))
    return found


def _consumer_ids_from_evidence(finding: dict[str, Any]) -> list[str]:
    found: list[str] = []
    for row in finding.get("evidence") or []:
        if isinstance(row, dict):
            data = row.get("structured_data") if isinstance(row.get("structured_data"), dict) else {}
            found.extend(_consumer_ids(data))
    return found


def _ids_for_kind(catalog: list[dict[str, Any]], kind: str) -> list[Any]:
    return [item.get("evidence_id") for item in catalog if item.get("kind") == kind and item.get("evidence_id")]


def _path_safe(path: str) -> bool:
    text = path.replace("\\", "/")
    if text.startswith("/") or (len(text) > 1 and text[1] == ":"):
        return False
    return ".." not in text.split("/")


def _line_plausible(line: Any) -> bool:
    return isinstance(line, int) and not isinstance(line, bool) and 1 <= line <= 1_000_000
