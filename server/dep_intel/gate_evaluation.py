"""Offline evaluation of labelled Boundary Gate scenarios.

Metrics are computed from the scenarios that actually run.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from dep_intel.gate_validation_corpus import scenarios
from dep_intel.review_commentary import attach_commentary
from dep_intel.review_explain import attach_explanation
from dep_intel.review_gate import decide_gate
from dep_intel.review_gate_rules import VALIDATOR_VERSION
from dep_intel.review_gate_validation import validate_gate
from dep_intel.store import now

LABELS = ("pass", "warn", "block", "incomplete")

FALSE_BLOCK_DEFINITION = (
    "Cases whose expected decision is not BLOCK, and whose gate decision is BLOCK, "
    "divided by all cases whose expected decision is not BLOCK."
)
FALSE_PASS_DEFINITION = (
    "Cases expected to be BLOCK or INCOMPLETE whose gate decision is PASS, "
    "divided by all cases whose expected decision is not PASS."
)


def evaluate_corpus(items: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = []
    invariant_passed = 0
    invariant_total = 0
    traceable = 0
    traced = 0
    replay_matches = 0
    for scenario in items if items is not None else scenarios():
        report = run_scenario(scenario)
        validation = report.get("gate_validation") or {}
        again = validate_gate(report, scenario.get("config"))
        predicted = (report.get("gate") or {}).get("decision")
        triggered = [item.get("rule_id") for item in (report.get("gate") or {}).get("decision_rules") or []]
        expected_rules = list(scenario.get("expected_rules") or [])
        rules_ok = all(rule_id in triggered for rule_id in expected_rules)
        evidence_ids = {item.get("evidence_id") for item in report.get("evidence_catalog") or []}
        for rule in (report.get("gate") or {}).get("decision_rules") or []:
            traced += 1
            refs = [ref for ref in rule.get("evidence_refs") or [] if ref]
            blocking = bool(rule.get("blocks"))
            if all(ref in evidence_ids for ref in refs) and (not blocking or refs):
                traceable += 1
        invariants = validation.get("invariants") or {}
        invariant_passed += int(invariants.get("passed") or 0)
        invariant_total += int(invariants.get("total") or 0)
        validated_rules = [item.get("rule_id") for item in validation.get("rule_validation") or [] if item.get("status") == "validated"]
        again_rules = [item.get("rule_id") for item in again.get("rule_validation") or [] if item.get("status") == "validated"]
        replay_ok = (
            again.get("reproducibility", {}).get("gate_input_hash") == (validation.get("reproducibility") or {}).get("gate_input_hash")
            and again.get("expected_decision") == validation.get("expected_decision")
            and again_rules == validated_rules
        )
        if replay_ok:
            replay_matches += 1
        failure = None
        if predicted != scenario["expected_decision"] or not rules_ok or validation.get("status") == "failed" or not replay_ok:
            failure = {
                "name": scenario["name"],
                "expected": scenario["expected_decision"],
                "predicted": predicted,
                "expected_rules": expected_rules,
                "triggered_rules": triggered,
                "validation_status": validation.get("status"),
                "validation_failures": validation.get("failures") or [],
                "replay_ok": replay_ok,
            }
        rows.append({
            "name": scenario["name"],
            "expected": scenario["expected_decision"],
            "predicted": predicted,
            "rules_ok": rules_ok,
            "validation_status": validation.get("status"),
            "failure": failure,
        })
    return _metrics(rows, invariant_passed, invariant_total, traceable, traced, replay_matches)


def run_scenario(scenario: dict[str, Any]) -> dict[str, Any]:
    """Run the gate, then explanation, validation, and commentary on one labelled input."""
    config = scenario.get("config") or {}
    alignment = str((scenario.get("change") or {}).get("graph_alignment") or "exact")
    gate = decide_gate(
        list(scenario.get("findings") or []),
        list(scenario.get("verification") or []),
        config,
        alignment=alignment,
        diagnostics=list(scenario.get("diagnostics") or []),
    )
    report = {
        "review_id": scenario.get("name"),
        "schema_version": 1,
        "gate": {key: value for key, value in gate.items() if key != "findings"},
        "findings": gate.get("findings") or [],
        "change": scenario.get("change") or {},
        "verification": scenario.get("verification") or [],
        "diagnostics": scenario.get("diagnostics") or [],
        "contract_changes": scenario.get("contract_changes") or [],
        "policies": [],
        "impact": scenario.get("impact") or {"affected_services": [], "ranked_candidates": []},
        "limitations": [],
        "review_control": {"pre_gate_abort": False, "cancelled": False},
    }
    report = attach_explanation(report, config)
    validation = validate_gate(report, config)
    report = dict(report)
    report["gate_validation"] = validation
    return attach_commentary(report)


def write_evaluation(payload: dict[str, Any], output_dir: Path) -> Path:
    directory = output_dir / "gate-validation"
    runs = directory / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    stamp = now().replace(":", "").replace("-", "").replace("+00:00", "Z")
    latest = directory / "latest.json"
    dated = runs / f"{stamp}.json"
    text = json.dumps(payload, indent=2, sort_keys=True)
    latest.write_text(text, encoding="utf-8")
    dated.write_text(text, encoding="utf-8")
    return latest


def _metrics(rows: list[dict[str, Any]], invariant_passed: int, invariant_total: int, traceable: int, traced: int, replay_matches: int) -> dict[str, Any]:
    matrix = {expected: {predicted: 0 for predicted in LABELS} for expected in LABELS}
    for row in rows:
        expected = row["expected"] if row["expected"] in matrix else "incomplete"
        predicted = row["predicted"] if row["predicted"] in LABELS else "incomplete"
        matrix[expected][predicted] += 1
    per_class = {}
    for label in LABELS:
        tp = matrix[label][label]
        predicted_count = sum(matrix[expected][label] for expected in LABELS)
        expected_count = sum(matrix[label][predicted] for predicted in LABELS)
        precision = tp / predicted_count if predicted_count else 0.0
        recall = tp / expected_count if expected_count else 0.0
        f1 = (2 * precision * recall / (precision + recall)) if precision + recall else 0.0
        per_class[label] = {"precision": precision, "recall": recall, "f1": f1, "support": expected_count}
    correct = sum(matrix[label][label] for label in LABELS)
    safe = [row for row in rows if row["expected"] != "block"]
    false_blocks = [row for row in safe if row["predicted"] == "block"]
    not_pass = [row for row in rows if row["expected"] != "pass"]
    false_passes = [row for row in rows if row["expected"] in {"block", "incomplete"} and row["predicted"] == "pass"]
    rule_hits = sum(1 for row in rows if row["rules_ok"])
    failures = [row["failure"] for row in rows if row["failure"]]
    return {
        "schema": "nami.gate.validation",
        "schema_version": 1,
        "scenario_count": len(rows),
        "validator_version": VALIDATOR_VERSION,
        "generated_at": now(),
        "metrics": {
            "decision_accuracy": correct / len(rows) if rows else 0.0,
            "per_class": per_class,
            "false_block_rate": len(false_blocks) / len(safe) if safe else 0.0,
            "false_pass_rate": len(false_passes) / len(not_pass) if not_pass else 0.0,
            "rule_activation_accuracy": rule_hits / len(rows) if rows else 0.0,
            "evidence_traceability_coverage": traceable / traced if traced else 0.0,
            "invariant_pass_rate": invariant_passed / invariant_total if invariant_total else 0.0,
            "replay_reproducibility_rate": replay_matches / len(rows) if rows else 0.0,
        },
        "definitions": {
            "false_block_rate": FALSE_BLOCK_DEFINITION,
            "false_pass_rate": FALSE_PASS_DEFINITION,
            "decision_accuracy": "Gate decisions matching the labelled expected decision, divided by scenario count.",
            "rule_activation_accuracy": "Scenarios whose expected rule ids are all present in the triggered rules, divided by scenario count.",
            "evidence_traceability_coverage": "Triggered rules whose evidence refs resolve, with blocking rules also requiring at least one ref, divided by triggered rules.",
            "invariant_pass_rate": "Passed invariant checks across scenarios, divided by invariant checks run.",
            "replay_reproducibility_rate": "Scenarios whose second validation replay matched the gate input hash, expected decision, and validated rules.",
            "counterfactuals": "Counterfactual checks replay a stored rule with one input changed. They check rule behavior, not a real-world causal outcome.",
        },
        "confusion_matrix": {"labels": list(LABELS), "counts": [[matrix[expected][predicted] for predicted in LABELS] for expected in LABELS]},
        "failures": failures,
        "scenarios": [{"name": row["name"], "expected": row["expected"], "predicted": row["predicted"], "validation_status": row["validation_status"]} for row in rows],
    }


def format_table(payload: dict[str, Any]) -> str:
    labels = payload["confusion_matrix"]["labels"]
    counts = payload["confusion_matrix"]["counts"]
    header = "expected\\predicted".ljust(22) + "".join(label.rjust(12) for label in labels)
    lines = [header]
    for index, label in enumerate(labels):
        lines.append(label.ljust(22) + "".join(str(value).rjust(12) for value in counts[index]))
    metrics = payload["metrics"]
    lines.extend([
        "",
        f"scenarios: {payload['scenario_count']}",
        f"decision accuracy: {metrics['decision_accuracy']:.3f}",
        f"false-block rate: {metrics['false_block_rate']:.3f}",
        f"false-pass rate: {metrics['false_pass_rate']:.3f}",
        f"rule activation accuracy: {metrics['rule_activation_accuracy']:.3f}",
        f"evidence traceability: {metrics['evidence_traceability_coverage']:.3f}",
        f"invariant pass rate: {metrics['invariant_pass_rate']:.3f}",
        f"replay reproducibility: {metrics['replay_reproducibility_rate']:.3f}",
        f"failures: {len(payload['failures'])}",
    ])
    for item in payload["failures"]:
        lines.append(f"- {item['name']}: expected {item['expected']} predicted {item['predicted']} validation {item['validation_status']} {item.get('validation_failures')}")
    return "\n".join(lines)
