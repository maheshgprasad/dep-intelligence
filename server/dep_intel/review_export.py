"""Canonical exports of a stored review.

Exports read the saved report. They do not run Nami Rank or the gate again.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import zipfile
from pathlib import Path
from typing import Any

from dep_intel.hybrid_impact import NRM_VERSION
from dep_intel.review_models import NBG_VERSION
from dep_intel.review_pdf import render_text_pdf
from dep_intel.review_store import read_review, render_markdown, review_dir
from dep_intel.sources import redact
from dep_intel.store import read_json, write_json

log = logging.getLogger("nami.boundary")

SCHEMA = "nami.review.export"
SCHEMA_VERSION = 1
NAMI_TRACE_VERSION = "0.1.0"
_DROP_KEYS = {"old_text", "new_text", "diff_text", "token", "api_key", "secret", "password", "authorization"}


def export_json(output_dir: Path, review_id: str) -> dict[str, Any]:
    report = read_review(output_dir, review_id)
    if report is None:
        raise FileNotFoundError(review_id)
    directory = review_dir(output_dir, review_id)
    manifest = read_json(directory, "manifest.json")
    feedback = read_json(directory, "feedback.json")
    payload = canonical_export(report, manifest if isinstance(manifest, dict) else {}, feedback if isinstance(feedback, dict) else {})
    return payload


EXPORT_RENDERER = "decision-logic-1"


def ensure_export(output_dir: Path, review_id: str, kind: str) -> Path:
    """Return a cached export file. kind is json, pdf, markdown, or zip."""
    report = read_review(output_dir, review_id)
    if report is None:
        raise FileNotFoundError(review_id)
    directory = review_dir(output_dir, review_id)
    manifest = read_json(directory, "manifest.json")
    feedback = read_json(directory, "feedback.json")
    payload = canonical_export(report, manifest if isinstance(manifest, dict) else {}, feedback if isinstance(feedback, dict) else {})
    token = {
        "report_hash": payload["reproducibility"]["report_hash"],
        "renderer": EXPORT_RENDERER,
        "feedback": [item.get("created_at") for item in (feedback or {}).get("items") or []] if isinstance(feedback, dict) else [],
    }
    cache = directory / "exports"
    cache.mkdir(parents=True, exist_ok=True)
    marker = cache / "cache.json"
    current = read_json(cache, "cache.json")
    fresh = isinstance(current, dict) and current.get("token") == token
    names = {"json": "report.json", "pdf": "report.pdf", "markdown": "report.md", "zip": "evidence.zip"}
    if kind not in names:
        raise ValueError(kind)
    target = cache / names[kind]
    if fresh and target.is_file():
        return target
    (cache / "report.json").write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    (cache / "report.md").write_text(render_markdown(report), encoding="utf-8")
    (cache / "report.pdf").write_bytes(render_text_pdf(pdf_lines(payload)))
    (cache / "evidence.zip").write_bytes(build_zip(review_id, payload, report))
    write_json(cache, "cache.json", {"token": token})
    log.info("export.generated", extra={"review_id": review_id, "kind": kind})
    return target


def canonical_export(report: dict[str, Any], manifest: dict[str, Any] | None = None, feedback: dict[str, Any] | None = None) -> dict[str, Any]:
    change = report.get("change") or {}
    gate = report.get("gate") or {}
    meta = manifest or {}
    later = list((feedback or {}).get("items") or []) if isinstance(feedback, dict) else []
    actions = report.get("actions") or []
    payload = {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "report_schema_version": SCHEMA_VERSION,
        "identity": {
            "review_id": report.get("review_id"),
            "nami_trace_version": NAMI_TRACE_VERSION,
            "nbg_version": report.get("module_version") or NBG_VERSION,
            "nrm_version": NRM_VERSION,
            "generated_at": meta.get("completed_at"),
            "repository": change.get("repository"),
            "service": change.get("service_id"),
            "source_kind": (change.get("source") or {}).get("kind") if isinstance(change.get("source"), dict) else None,
            "base_sha": change.get("base_sha"),
            "head_sha": change.get("head_sha"),
            "graph_version": change.get("graph_version"),
            "graph_alignment": change.get("graph_alignment"),
            "config_hash": meta.get("config_hash"),
            "temporal_cutoff": change.get("temporal_cutoff"),
            "requested_review_mode": report.get("requested_mode"),
            "resolved_review_mode": report.get("resolved_mode"),
        },
        "gate_decision": gate.get("decision"),
        "decision_presentation": report.get("decision_presentation"),
        "enforcement": report.get("enforcement"),
        "assessment": report.get("assessment"),
        "gate_checklist": report.get("gate_checklist") or [],
        "inferences": report.get("inferences") or [],
        "decision_rules": gate.get("decision_rules") or [],
        "required_actions": [item for item in actions if item.get("required")],
        "recommended_actions": [item for item in actions if not item.get("required")],
        "findings": _public_findings(report.get("findings") or []),
        "evidence": report.get("evidence_catalog") or [],
        "counter_evidence": report.get("counter_evidence") or [],
        "suppressed_findings": report.get("suppressed_findings") or [],
        "impact": _public_impact(report.get("impact") or {}),
        "contract_changes": report.get("contract_changes") or [],
        "policies": report.get("policies") or [],
        "verification": _public_verification(report.get("verification") or []),
        "diagnostics": report.get("diagnostics") or [],
        "limitations": report.get("limitations") or [],
        "feedback": {
            "note": "Later human feedback does not change the original gate decision.",
            "original_decision": gate.get("decision"),
            "items": later,
        },
        "copy_summary": report.get("copy_summary"),
        "gate_validation": report.get("gate_validation") or {},
        "commentary": _public_commentary(report.get("commentary") or {}),
        "reproducibility": {
            "review_id": report.get("review_id"),
            "base_sha": change.get("base_sha"),
            "head_sha": change.get("head_sha"),
            "graph_version": change.get("graph_version"),
            "config_hash": meta.get("config_hash"),
            "gate_input_hash": ((report.get("gate_validation") or {}).get("reproducibility") or {}).get("gate_input_hash") or "",
            "gate_input_hash_note": "Integrity fingerprint of the deterministic Gate inputs. It excludes commentary and is not a digital signature.",
            "report_hash": "",
            "report_hash_note": "Integrity fingerprint of this export, including commentary. It is not a digital signature.",
        },
    }
    scrubbed = _scrub(payload)
    scrubbed["reproducibility"]["report_hash"] = _hash(scrubbed)
    return scrubbed


def pdf_lines(payload: dict[str, Any]) -> list[str]:
    identity = payload.get("identity") or {}
    assessment = payload.get("assessment") or {}
    enforcement = payload.get("enforcement") or {}
    decision = str(payload.get("gate_decision") or "incomplete").upper()
    lines = [
        "Nami Trace",
        "Change Intelligence Report",
        "",
        f"Decision: {decision}",
        f"Repository: {identity.get('repository') or ''}",
        f"Service: {identity.get('service') or ''}",
        f"Change: {identity.get('base_sha') or ''} -> {identity.get('head_sha') or ''}",
        f"Generated: {identity.get('generated_at') or ''}",
        f"Review ID: {identity.get('review_id') or ''}",
        "",
        "Executive assessment",
        assessment.get("headline") or "",
        assessment.get("summary") or "",
        f"Can this change proceed? {decision}",
        "",
        "Nami explanation",
    ]
    commentary = payload.get("commentary") or {}
    if commentary:
        lines.extend([
            commentary.get("headline") or "",
            commentary.get("summary") or "",
            "Why this matters",
            commentary.get("why_it_matters") or "",
        ])
        if not (payload.get("required_actions") or []):
            lines.extend(["What to do next", commentary.get("next_action") or ""])
        lines.append(commentary.get("validation_comment") or "")
    else:
        lines.append("No explanation was recorded.")
    lines.extend([
        "",
        "Required actions",
    ])
    required = payload.get("required_actions") or []
    lines.extend([f"- {item.get('title')}: {item.get('description')}" for item in required] or ["None."])
    lines.extend(["", "Decision logic validation"])
    lines.extend(_validation_lines(payload.get("gate_validation") or {}))
    lines.extend([
        "",
        "Enforcement",
        f"Enforcement: {enforcement.get('enforcement_mode') or 'advisory'}",
        f"Workflow prevented: {'Yes' if enforcement.get('workflow_prevented') else 'No'}",
        "",
        "Checklist",
    ])
    for item in payload.get("gate_checklist") or []:
        lines.append(f"- {item.get('label')}: {item.get('status')}")
    lines.extend(["", "Recommended actions"])
    recommended = payload.get("recommended_actions") or []
    lines.extend([f"- {item.get('title')}: {item.get('description')}" for item in recommended] or ["None."])
    lines.extend(["", "Why this decision"])
    for rule in payload.get("decision_rules") or []:
        lines.append(f"- {rule.get('title')} ({rule.get('rule_id')}) evidence {rule.get('evidence_grade')}")
    lines.extend(["", "Inferences"])
    for item in payload.get("inferences") or []:
        lines.append(f"- {item.get('title')}: {item.get('statement')}")
    lines.extend(["", "Evidence"])
    for item in (payload.get("evidence") or [])[:40]:
        where = item.get("file_path") or ""
        lines.append(f"- {item.get('evidence_id')}: {item.get('statement')} [{item.get('source_label') or item.get('source')}] {where}")
    lines.extend(["", "Counter-evidence"])
    counter = payload.get("counter_evidence") or []
    lines.extend([f"- {item.get('evidence_id')}: {item.get('statement')}" for item in counter] or ["None recorded."])
    lines.extend(["", "Suppressed claims"])
    suppressed = payload.get("suppressed_findings") or []
    lines.extend([f"- {item.get('potential_claim')}: {item.get('reason')}" for item in suppressed] or ["None."])
    lines.extend(["", "Affected services"])
    services = (payload.get("impact") or {}).get("affected_services") or []
    lines.extend([f"- {item}" for item in services] or ["None."])
    lines.extend(["", "Verification"])
    verification = payload.get("verification") or []
    lines.extend([f"- {item.get('id')}: {item.get('status')} exit {item.get('exit_code')}" for item in verification] or ["Not evaluated."])
    lines.extend(["", "Limitations"])
    for item in payload.get("limitations") or []:
        lines.append(f"- {item}")
    lines.extend([
        "",
        "How this result was produced",
        "Change view, evidence collection, evidence sufficiency, inference, Boundary Gate, independent validation, commentary.",
        "Commentary does not change the Gate decision.",
        "",
        "Reproducibility",
        "Gate input fingerprint. Integrity fingerprint, not digital signature.",
        str((payload.get("reproducibility") or {}).get("gate_input_hash") or ""),
        "Report fingerprint. This covers the export, including commentary. It is not a digital signature.",
        str((payload.get("reproducibility") or {}).get("report_hash") or ""),
    ])
    feedback = (payload.get("feedback") or {}).get("items") or []
    if feedback:
        lines.extend(["", "Post-review feedback", "Original decision is unchanged."])
        for item in feedback:
            lines.append(f"- {item.get('finding_fingerprint')}: {item.get('status')} at {item.get('created_at')}")
    return lines


def build_zip(review_id: str, payload: dict[str, Any], report: dict[str, Any]) -> bytes:
    root = f"nami-review-{review_id}"
    files = {
        f"{root}/report.pdf": render_text_pdf(pdf_lines(payload)),
        f"{root}/report.md": render_markdown(report).encode("utf-8"),
        f"{root}/report.json": _bytes(payload),
        f"{root}/evidence/evidence.json": _bytes(payload.get("evidence") or []),
        f"{root}/evidence/counter-evidence.json": _bytes(payload.get("counter_evidence") or []),
        f"{root}/evidence/suppressed-findings.json": _bytes(payload.get("suppressed_findings") or []),
        f"{root}/impact/ranked-impact.json": _bytes((payload.get("impact") or {}).get("ranked_candidates") or []),
        f"{root}/impact/structural-paths.json": _bytes(_paths(payload)),
        f"{root}/impact/temporal-evidence.json": _bytes(_temporal(payload)),
        f"{root}/gate/decision.json": _bytes({"decision": payload.get("gate_decision"), "enforcement": payload.get("enforcement"), "rules": payload.get("decision_rules")}),
        f"{root}/gate/checklist.json": _bytes(payload.get("gate_checklist") or []),
        f"{root}/gate/inferences.json": _bytes(payload.get("inferences") or []),
        f"{root}/gate/rules.json": _bytes(payload.get("decision_rules") or []),
        f"{root}/gate/actions.json": _bytes({"required": payload.get("required_actions"), "recommended": payload.get("recommended_actions")}),
        f"{root}/verification/verification.json": _bytes(payload.get("verification") or []),
        f"{root}/change/change-view.json": _bytes({"identity": payload.get("identity"), "contracts": payload.get("contract_changes")}),
        f"{root}/validation/gate-validation.json": _bytes(payload.get("gate_validation") or {}),
        f"{root}/validation/invariants.json": _bytes((payload.get("gate_validation") or {}).get("invariants") or {}),
        f"{root}/validation/counterfactuals.json": _bytes((payload.get("gate_validation") or {}).get("counterfactuals") or {}),
        f"{root}/validation/reproducibility.json": _bytes((payload.get("gate_validation") or {}).get("reproducibility") or {}),
        f"{root}/commentary/commentary.json": _bytes(payload.get("commentary") or {}),
        f"{root}/metadata/reproducibility.json": _bytes(payload.get("reproducibility") or {}),
        f"{root}/metadata/manifest.json": _bytes({"files": sorted(str(name) for name in [])}),
    }
    names = sorted(files)
    files[f"{root}/metadata/manifest.json"] = _bytes({"files": [name.split("/", 1)[1] for name in names if not name.endswith("/metadata/manifest.json")] + ["metadata/manifest.json"]})
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(files):
            if _forbidden_name(name):
                continue
            archive.writestr(name, files[name])
    return buffer.getvalue()


def _public_commentary(commentary: dict[str, Any]) -> dict[str, Any]:
    if not commentary:
        return {}
    kept = {
        "headline": commentary.get("headline") or "",
        "summary": commentary.get("summary") or "",
        "why_it_matters": commentary.get("why_it_matters") or "",
        "next_action": commentary.get("next_action") or "",
        "evidence_comment": commentary.get("evidence_comment") or "",
        "validation_comment": commentary.get("validation_comment") or "",
        "limitation_note": commentary.get("limitation_note") or "",
        "advisory": True,
        "source": commentary.get("source") or "",
        "mode": commentary.get("mode") or "",
        "purpose": commentary.get("purpose") or "commentary",
        "provider": commentary.get("provider") or "",
        "model": commentary.get("model") or "",
        "generated_at": commentary.get("generated_at") or commentary.get("commentary_generated_at") or "",
    }
    if commentary.get("model_diagnostic"):
        kept["model_diagnostic"] = commentary.get("model_diagnostic")
    return kept


def _validation_lines(validation: dict[str, Any]) -> list[str]:
    if not validation:
        return ["Validation was not recorded."]
    labels = {
        "validated": "VALIDATED",
        "validated_with_limitations": "VALIDATED WITH LIMITATIONS",
        "failed": "VALIDATION FAILED",
    }
    status = labels.get(str(validation.get("status") or ""), str(validation.get("status") or ""))
    observed = str(validation.get("observed_decision") or "").upper()
    expected = str(validation.get("expected_decision") or "").upper()
    match = "Confirmed" if validation.get("decision_match") else "Not confirmed"
    lines = [
        f"Validation status: {status}",
        f"Observed decision: {observed}",
        f"Independent replay: {expected}",
        f"Decision consistency: {match}",
        validation.get("summary") or "",
    ]
    for rule in validation.get("rule_validation") or []:
        if rule.get("status") != "validated":
            continue
        lines.append(f"Triggered rule: {rule.get('rule_id')} {rule.get('title')}")
        lines.append("Evidence requirements")
        for item in rule.get("requirements") or []:
            mark = "Yes" if item.get("status") == "satisfied" else "No"
            lines.append(f"- {mark}. {item.get('label') or item.get('requirement')}")
        if rule.get("evidence_grade"):
            lines.append(f"- Evidence grade {rule.get('evidence_grade')}")
        if rule.get("counter_evidence_status") == "none_decisive":
            lines.append("- No decisive counter-evidence")
        elif rule.get("counter_evidence_status") == "decisive_contradiction":
            lines.append("- Decisive counter-evidence was recorded")
    invariants = validation.get("invariants") or {}
    counterfactuals = validation.get("counterfactuals") or {}
    lines.append(f"Safety checks: {invariants.get('passed', 0)} / {invariants.get('total', 0)} passed")
    if counterfactuals.get("total"):
        lines.append(f"Counterfactual checks: {counterfactuals.get('passed', 0)} / {counterfactuals.get('total', 0)} passed")
    else:
        lines.append("Counterfactual checks: none applied to the triggered rules")
    fingerprint = (validation.get("reproducibility") or {}).get("gate_input_hash") or ""
    lines.append(f"Gate input fingerprint: {fingerprint}")
    lines.append("Integrity fingerprint, not digital signature.")
    for item in validation.get("limitations") or []:
        lines.append(f"Limitation: {item}")
    return lines


def _public_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    kept = []
    for item in findings:
        kept.append({
            "finding_id": item.get("finding_id"),
            "category": item.get("category"),
            "severity": item.get("severity"),
            "title": item.get("title"),
            "claim": item.get("claim"),
            "service_id": item.get("service_id"),
            "file_path": item.get("file_path"),
            "line_start": item.get("line_start"),
            "evidence_strength": item.get("evidence_strength"),
            "candidate_strength": item.get("candidate_strength"),
            "policy_id": item.get("policy_id"),
            "blocking": item.get("blocking"),
        })
    return kept


def _public_impact(impact: dict[str, Any]) -> dict[str, Any]:
    candidates = []
    for item in impact.get("ranked_candidates") or []:
        candidates.append({
            "service_id": item.get("service_id"),
            "file_path": item.get("file_path"),
            "name": item.get("name"),
            "evidence_origin": item.get("evidence_origin"),
            "hybrid_score": item.get("hybrid_score"),
            "structural_score": item.get("structural_score"),
            "temporal_score": item.get("temporal_score"),
            "statement": item.get("statement"),
            "structural": {"path": (item.get("structural") or {}).get("path") or []},
            "temporal": {"observation": (item.get("temporal") or {}).get("observation"), "score": (item.get("temporal") or {}).get("score")},
        })
    return {
        "affected_services": impact.get("affected_services") or [],
        "ranked_candidates": candidates,
        "score_note": impact.get("score_note") or "Ranking scores are not probabilities.",
        "graph_version": impact.get("graph_version"),
    }


def _public_verification(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "id": item.get("id"),
            "command": item.get("command"),
            "cwd": item.get("cwd"),
            "status": item.get("status"),
            "exit_code": item.get("exit_code"),
            "output": item.get("output") or "",
        }
        for item in rows
    ]


def _paths(payload: dict[str, Any]) -> list[Any]:
    rows = []
    for item in (payload.get("impact") or {}).get("ranked_candidates") or []:
        rows.append({"service_id": item.get("service_id"), "file_path": item.get("file_path"), "path": (item.get("structural") or {}).get("path") or []})
    return rows


def _temporal(payload: dict[str, Any]) -> list[Any]:
    rows = []
    for item in (payload.get("impact") or {}).get("ranked_candidates") or []:
        rows.append({"service_id": item.get("service_id"), "file_path": item.get("file_path"), "temporal": item.get("temporal")})
    return rows


def _scrub(value: Any) -> Any:
    if isinstance(value, str):
        if ".env" in value or "worktrees" in value:
            return "[omitted]"
        return redact(value)[:4000]
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    if isinstance(value, dict):
        return {key: _scrub(item) for key, item in value.items() if key not in _DROP_KEYS}
    return value


def _hash(payload: dict[str, Any]) -> str:
    clone = json.loads(json.dumps(payload, sort_keys=True, default=str))
    clone.get("reproducibility", {}).pop("report_hash", None)
    raw = json.dumps(clone, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _bytes(value: Any) -> bytes:
    return json.dumps(value, indent=2, sort_keys=True, default=str).encode("utf-8")


def _forbidden_name(name: str) -> bool:
    lowered = name.lower()
    return lowered.endswith(".env") or "/.env" in lowered or "worktree" in lowered or "/.git/" in lowered
