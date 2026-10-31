"""Point-in-time review cards stored inside an intelligence report.

The card is built from the review that existed at generation time.
Opening the report does not read newer reviews.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from dep_intel.path_hygiene import scrub_text
from dep_intel.review_explain import GATE_RULES
from dep_intel.review_store import read_review

_SOURCES = {
    "diff": "Code change",
    "CRG": "Code dependency graph",
    "cluster_graph": "Cross-service dependency graph",
    "OpenAPI": "API specification",
    "git_history": "Repository history",
    "verification_command": "Verification command",
    "policy": "Project rule",
    "NRM": "Nami Rank",
    "LLM": "Model suggestion",
}
_DECISION_ORDER = {"block": 0, "incomplete": 1, "warn": 2, "pass": 3}
_STRUCTURAL = ("BG-IMPACT", "BG-CONTRACT", "BG-POLICY")
_TEMPORAL = "This evidence alone does not confirm a current structural dependency."


def summarize_reviews(
    settings_output: Path,
    rows: list[dict[str, Any]],
    *,
    members: set[str] | None = None,
) -> dict[str, Any]:
    summaries = [summarize_review(settings_output, row, members=members) for row in rows]
    counts = {"pass": 0, "warn": 0, "block": 0, "incomplete": 0}
    for item in summaries:
        decision = str(item.get("decision") or "").lower()
        if decision in counts:
            counts[decision] += 1
    attention = [item for item in summaries if item.get("decision") in {"block", "incomplete", "warn"}]
    attention.sort(key=lambda item: _DECISION_ORDER.get(str(item.get("decision")), 9))
    recent = summaries[:20]
    return {
        "total_considered": len(summaries),
        "counts": counts,
        "detail_limit": 10,
        "detail_included": min(10, len(attention)),
        "detail_omitted": max(0, len(attention) - 10),
        "recent_reviews": recent,
        "attention_reviews": attention[:50],
        "reviews": attention[:50],
        "included": len(recent),
        "explained": len(attention),
        "note": "Review counts describe Nami Gate outcomes and are not a software-quality score. Detailed cards are the attention-bearing reviews.",
    }


def summarize_review(output_dir: Path, row: dict[str, Any], *, members: set[str] | None = None) -> dict[str, Any]:
    report = read_review(output_dir, str(row.get("review_id") or "")) or {}
    commentary = report.get("commentary") if isinstance(report.get("commentary"), dict) else {}
    assessment = report.get("assessment") if isinstance(report.get("assessment"), dict) else {}
    gate = report.get("gate") if isinstance(report.get("gate"), dict) else {}
    decision = str(row.get("decision") or gate.get("decision") or "incomplete").lower()
    rules = _rules(gate, decision)
    primary = _primary(rules, decision)
    reason_codes = [str(item) for item in (gate.get("reason_codes") or []) if item][:12]
    targets = _targets(report)
    evidence = _evidence(report, primary)
    why, action = _why_and_action(decision, primary, commentary, assessment, targets)
    validation = _validation(report, commentary)
    repository = _safe_repository(str(row.get("repository") or ""))
    card = {
        "review_id": row.get("review_id"),
        "decision": decision,
        "service": row.get("service") or "",
        "repository": repository,
        "completed_at": row.get("completed_at") or "",
        "headline": _plain(commentary.get("headline") or assessment.get("headline") or _headline(decision, primary)),
        "primary_rule": primary,
        "rules": rules[:8],
        "reason_codes": reason_codes,
        "targets": targets,
        "evidence_summary": evidence,
        "evidence": [evidence] if evidence.get("statement") else [],
        "what_happened": _plain(commentary.get("summary") or assessment.get("summary") or _happened(decision, primary, targets)),
        "why_it_matters": why,
        "next_action": action,
        "validation_status": validation["status"],
        "validation": validation["sentence"],
        "validation_detail": validation,
    }
    outside = _outside(report, members)
    if outside:
        card["cross_cluster_targets"] = outside
        card["cross_cluster_note"] = "Cross-cluster review target"
    return card


def _rules(gate: dict[str, Any], decision: str) -> list[dict[str, Any]]:
    stored = gate.get("decision_rules") if isinstance(gate.get("decision_rules"), list) else []
    triggered = []
    for item in stored:
        if not isinstance(item, dict):
            continue
        outcome = str(item.get("outcome") or "")
        if outcome in {"not_triggered", "skipped", "not_applicable"}:
            continue
        if outcome not in {"", "triggered"} and str(item.get("decision") or "").lower() != decision:
            continue
        triggered.append({
            "rule_id": str(item.get("rule_id") or ""),
            "title": str(item.get("title") or ""),
            "decision": str(item.get("decision") or decision),
        })
    if any(item.get("outcome") == "triggered" for item in stored if isinstance(item, dict)):
        triggered = [
            {
                "rule_id": str(item.get("rule_id") or ""),
                "title": str(item.get("title") or ""),
                "decision": str(item.get("decision") or decision),
            }
            for item in stored
            if isinstance(item, dict) and item.get("outcome") == "triggered"
        ]
    if triggered:
        return triggered
    return _rules_from_reason_codes(gate, decision)


_REASON_RULES = {
    "temporal_only_review_target": "BG-TEMPORAL-001",
    "contract_break": "BG-CONTRACT-001",
    "graph_stale": "BG-GRAPH-001",
    "graph_missing": "BG-GRAPH-001",
    "verification_unavailable": "BG-EVIDENCE-001",
    "evidence_incomplete": "BG-EVIDENCE-001",
}


def _rules_from_reason_codes(gate: dict[str, Any], decision: str) -> list[dict[str, Any]]:
    """Older reviews stored reason codes without a decision_rules list."""
    found = []
    seen = set()
    for code in gate.get("reason_codes") or []:
        rule_id = _REASON_RULES.get(str(code))
        if not rule_id or rule_id in seen:
            continue
        meta = GATE_RULES.get(rule_id)
        if not meta or meta.get("decision") != decision:
            continue
        seen.add(rule_id)
        found.append({"rule_id": rule_id, "title": meta["title"], "decision": decision})
    return found


def _primary(rules: list[dict[str, Any]], decision: str) -> dict[str, Any] | None:
    if not rules:
        return None
    if decision == "block":
        blocking = [item for item in rules if item.get("decision") == "block"]
        return (blocking or rules)[0]
    if decision == "incomplete":
        for rule_id in ("BG-GRAPH-001", "BG-EVIDENCE-001", "BG-VERIFY-001"):
            for item in rules:
                if item.get("rule_id") == rule_id:
                    return item
        return rules[0]
    if decision == "warn":
        def rank(item: dict[str, Any]) -> int:
            rule_id = str(item.get("rule_id") or "")
            if rule_id.startswith(_STRUCTURAL):
                return 0
            if "TEMPORAL" in rule_id:
                return 1
            return 2
        return sorted(rules, key=rank)[0]
    return rules[0]


def _targets(report: dict[str, Any]) -> dict[str, Any]:
    found: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    findings = report.get("findings") if isinstance(report.get("findings"), list) else []
    candidates = ((report.get("impact") or {}).get("ranked_candidates") or []) if isinstance(report.get("impact"), dict) else []
    changes = ((report.get("change") or {}).get("contract_changes") or []) if isinstance(report.get("change"), dict) else []
    sources = list(findings) + list(candidates) + list(changes)
    catalog = report.get("evidence_catalog") if isinstance(report.get("evidence_catalog"), list) else []
    sources.extend(catalog)
    for item in sources:
        if not isinstance(item, dict):
            continue
        service = str(item.get("service_id") or item.get("service") or "")
        file_path = _relative(str(item.get("file_path") or item.get("file") or ""))
        symbol = str(item.get("symbol") or item.get("name") or "")
        contract = str(item.get("contract") or "")
        if not file_path and not symbol and not contract:
            continue
        key = (service, file_path, symbol or contract)
        if key in seen:
            continue
        seen.add(key)
        row = {"service": service, "origin": str(item.get("evidence_origin") or item.get("source") or "")}
        if file_path:
            row["file"] = file_path
        if symbol and not _looks_like_path(symbol):
            row["symbol"] = symbol
        if contract:
            row["contract"] = contract
        found.append(row)
    unique_files = {item.get("file") for item in found if item.get("file")}
    return {
        "total_findings": len(findings),
        "unique_target_count": len(found),
        "unique_file_count": len(unique_files),
        "items": found[:20],
        "truncated": len(found) > 5,
        "omitted": max(0, len(found) - 5),
    }


def _evidence(report: dict[str, Any], primary: dict[str, Any] | None) -> dict[str, str]:
    rule_id = str((primary or {}).get("rule_id") or "")
    catalog = [item for item in (report.get("evidence_catalog") or []) if isinstance(item, dict)]
    preferred = _preferred_sources(rule_id)
    chosen = None
    for source in preferred:
        for item in catalog:
            if str(item.get("source") or "") == source and (item.get("statement") or item.get("summary")):
                chosen = item
                break
        if chosen:
            break
    if chosen is None:
        for item in catalog:
            if item.get("statement") or item.get("summary"):
                chosen = item
                break
    if chosen is None and "TEMPORAL" in rule_id:
        return {
            "source": "Repository history",
            "statement": "Repository history identified the files as temporal review targets.",
            "origin": "temporal_only",
            "grade": "",
        }
    if chosen is None:
        return {"source": "", "statement": "", "origin": "", "grade": ""}
    return {
        "source": _SOURCES.get(str(chosen.get("source") or ""), "Stored evidence"),
        "statement": _plain(chosen.get("statement") or chosen.get("summary") or ""),
        "origin": str(chosen.get("source") or ""),
        "grade": str(chosen.get("evidence_grade") or ""),
    }


def _preferred_sources(rule_id: str) -> tuple[str, ...]:
    if rule_id.startswith("BG-CONTRACT"):
        return ("OpenAPI", "cluster_graph", "diff")
    if "TEMPORAL" in rule_id:
        return ("git_history", "NRM")
    if rule_id.startswith("BG-VERIFY"):
        return ("verification_command",)
    if rule_id.startswith("BG-POLICY"):
        return ("policy", "diff")
    if rule_id.startswith("BG-IMPACT") or rule_id.startswith("BG-GRAPH"):
        return ("cluster_graph", "CRG")
    return ()


def _why_and_action(
    decision: str,
    primary: dict[str, Any] | None,
    commentary: dict[str, Any],
    assessment: dict[str, Any],
    targets: dict[str, Any],
) -> tuple[str, str]:
    why = _plain(commentary.get("why_it_matters") or "")
    action = _plain(commentary.get("next_action") or assessment.get("recommended_next_step") or "")
    rule_id = str((primary or {}).get("rule_id") or "")
    if "TEMPORAL" in rule_id:
        if _TEMPORAL not in why:
            why = _TEMPORAL
        if not action:
            action = _file_action(targets)
    if decision in {"warn", "block", "incomplete"}:
        if not why:
            why = _fallback_why(decision)
        if not action:
            action = _fallback_action(decision)
    return why, action


def _validation(report: dict[str, Any], commentary: dict[str, Any]) -> dict[str, str]:
    if "gate_validation" not in report:
        return {
            "status": "not_available",
            "label": "Decision logic validation: Not available",
            "note": "This review was created before a stored independent validation result was available.",
            "sentence": "Decision logic validation: Not available. This review was created before a stored independent validation result was available.",
        }
    validation = report.get("gate_validation")
    status = str(validation.get("status") or "") if isinstance(validation, dict) else ""
    if not isinstance(validation, dict) or not status:
        return {
            "status": "unavailable",
            "label": "Decision logic validation: Unavailable",
            "note": "The expected validation record is missing or incomplete.",
            "sentence": "Decision logic validation: Unavailable. The expected validation record is missing or incomplete.",
        }
    labels = {
        "validated": "Decision logic validated",
        "validated_with_limitations": "Decision logic validated with limitations",
        "failed": "Decision validation failed",
    }
    label = labels.get(status, status)
    comment = _plain(commentary.get("validation_comment") or "")
    if comment and label.lower() not in comment.lower():
        sentence = f"{label}. {comment}"
    else:
        sentence = comment or label
    return {"status": status, "label": label, "note": "", "sentence": sentence}


def _headline(decision: str, primary: dict[str, Any] | None) -> str:
    if primary and primary.get("title"):
        return str(primary["title"])
    return {"block": "Action required", "incomplete": "Current evidence is insufficient", "warn": "Review recommended", "pass": "No verified blocker"}.get(decision, "Stored review")


def _happened(decision: str, primary: dict[str, Any] | None, targets: dict[str, Any]) -> str:
    title = str((primary or {}).get("title") or "A stored Gate rule")
    files = targets.get("unique_file_count") or 0
    findings = targets.get("total_findings") or 0
    if files:
        return f"{title}. {files} files flagged. {findings} supporting findings."
    return title if decision == "pass" else f"{title}."


def _fallback_why(decision: str) -> str:
    if decision == "incomplete":
        return "Nami does not have the evidence required to finish this decision."
    if decision == "block":
        return "A verified project rule matched this change."
    return "Review evidence exists, and this result is not a confirmed structural dependency by itself."


def _fallback_action(decision: str) -> str:
    if decision == "incomplete":
        return "Refresh intelligence or run an Exact review."
    if decision == "block":
        return "Restore compatibility or update the affected consumer, then rerun the review."
    return "Review the listed targets before proceeding."


def _file_action(targets: dict[str, Any]) -> str:
    files = [str(item.get("file")) for item in targets.get("items") or [] if item.get("file")]
    if not files:
        return "Review the associated files before deployment."
    if len(files) == 1:
        return f"Review {files[0]} before deployment."
    return f"Review {files[0]} and {len(files) - 1} other files before deployment."


def _outside(report: dict[str, Any], members: set[str] | None) -> list[str]:
    if not members:
        return []
    impact = report.get("impact") if isinstance(report.get("impact"), dict) else {}
    names = [str(item) for item in (impact.get("affected_services") or []) if item and str(item) not in members]
    return names[:8]


def _safe_repository(repository: str) -> str:
    absolute = repository.startswith("/") or (len(repository) > 2 and repository[1] == ":")
    cleaned = scrub_text(repository)
    if absolute:
        return Path(cleaned).name or Path(repository).name
    return cleaned or repository


def _relative(path: str) -> str:
    cleaned = scrub_text(path).lstrip("/")
    if cleaned.startswith("/") or ":\\" in cleaned:
        return ""
    return cleaned


def _looks_like_path(value: str) -> bool:
    return "/" in value or "\\" in value


def _plain(value: Any) -> str:
    return " ".join(scrub_text(str(value or "")).split())[:400]
