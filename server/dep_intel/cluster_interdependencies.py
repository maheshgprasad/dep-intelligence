"""Deterministic cluster change-set and interdependency correlation.

This module does not rank files, run Boundary Gate, or rewrite a child review.
Temporal association is supporting context and cannot classify a relationship
as broken.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Any

STRUCTURAL = {"extracted", "declared", "openapi", "generated topology", "override", "source extraction", "source_extraction", "confirmed", "cluster_graph", "http", "grpc", "event"}
TEMPORAL = {"temporal", "temporal_only", "history", "cochange"}

CLASS_ORDER = (
    "broken",
    "potentially_broken",
    "coordinated_change",
    "resolved_by_change_set",
    "relationship_removed",
    "introduced",
    "modified",
    "unchanged",
)


def analyze_interdependencies(
    members: list[dict[str, Any]],
    relationships: list[dict[str, Any]] | None = None,
    *,
    comparison_mode: str = "evidence_derived",
    source: dict[str, Any] | None = None,
    cluster_id: str = "",
    before_relationships: list[dict[str, Any]] | None = None,
    after_relationships: list[dict[str, Any]] | None = None,
    before_state: dict[str, Any] | None = None,
    after_state: dict[str, Any] | None = None,
    comparison_coverage: dict[str, Any] | None = None,
    limitations: list[str] | None = None,
) -> dict[str, Any]:
    """Classify relationships between the services in one cluster change set.

    When before and after dependency states are present, provider presence and
    consumer references come from those states. Published edges are not a
    substitute for that comparison.
    """
    rels = [item for item in (relationships or []) if isinstance(item, dict)]
    temporal = [item for item in rels if _temporal(item)]
    actions = _actions(members)
    mode = comparison_mode if comparison_mode in {"exact_boundary_snapshots", "evidence_derived", "partial"} else "evidence_derived"
    coverage = dict(comparison_coverage or {})
    notes = list(limitations or [])
    if before_state is not None and after_state is not None:
        if before_relationships is not None:
            temporal.extend(item for item in before_relationships if isinstance(item, dict) and _temporal(item))
        items = _classify_states(before_state, after_state, actions, members, mode)
        if _states_partial(before_state, after_state):
            mode = "partial"
    elif before_relationships is not None and after_relationships is not None:
        mode = "exact_boundary_snapshots" if mode != "partial" else "partial"
        before_facts = _facts_from_edges([item for item in before_relationships if isinstance(item, dict) and not _temporal(item)])
        after_facts = _facts_from_edges([item for item in after_relationships if isinstance(item, dict) and not _temporal(item)])
        temporal.extend(item for item in before_relationships if isinstance(item, dict) and _temporal(item))
        items = _classify_facts(before_facts, after_facts, actions, members, mode, _edges(before_relationships, {"provider_removed": []}) + _edges(after_relationships, {"provider_removed": []}))
    else:
        if before_relationships is not None or after_relationships is not None or before_state is not None or after_state is not None:
            mode = "partial"
        structural = [item for item in rels if not _temporal(item)]
        if not structural and after_relationships is not None:
            structural = [item for item in after_relationships if isinstance(item, dict) and not _temporal(item)]
        edges = _edges(structural, actions)
        items = []
        seen: set[tuple[str, str, str]] = set()
        for edge in edges:
            key = (edge["consumer"], edge["provider"], edge["contract"])
            if key in seen:
                continue
            seen.add(key)
            items.append(_classify(edge, actions, structural))
        items.extend(_introduced(actions, seen))
    _assign_evidence(items, members)
    items.sort(key=lambda item: (CLASS_ORDER.index(item["classification"]) if item["classification"] in CLASS_ORDER else 99, item.get("provider_service") or item["source_service"], item["contract_id"], item.get("consumer_service") or item["target_service"]))
    summary = _summary(items)
    changed_services = sorted({str(item.get("service_id") or "") for item in members if item.get("changed")})
    child_affected = {str(service) for item in members for service in item.get("affected_services") or [] if service}
    affected = sorted({service for item in items if item["classification"] != "unchanged" for service in item.get("affected_services") or []} | child_affected)
    unchanged_affected = sorted(service for service in affected if service not in set(changed_services))
    paths = [item.get("paths") or [] for item in items if item["classification"] != "unchanged"]
    change_set = {
        "cluster_id": cluster_id,
        "source_kind": str((source or {}).get("kind") or ""),
        "since": str((source or {}).get("since") or ""),
        "until": str((source or {}).get("until") or ""),
        "repositories": [_change_row(item) for item in members],
    }
    evidence = [item["cluster_evidence"] for item in items if item.get("cluster_evidence")]
    return {
        "comparison_mode": mode,
        "comparison_coverage": coverage,
        "limitations": notes,
        "change_set": change_set,
        "summary": summary,
        "items": items,
        "temporal_context": [_temporal_note(item) for item in temporal],
        "changed_services": changed_services,
        "affected_services": affected,
        "unchanged_but_affected_services": unchanged_affected,
        "relationships_examined": summary["examined"],
        "dependency_paths": paths,
        "cluster_evidence": evidence,
        "before_state": _public_state(before_state),
        "after_state": _public_state(after_state),
    }


def fold_assessment(assessment: dict[str, Any], analysis: dict[str, Any], status: str) -> dict[str, Any]:
    """Add interdependency context. Does not invent a cluster Gate or rewrite child decisions."""
    folded = dict(assessment or {})
    summary = analysis.get("summary") or {}
    broken = int(summary.get("broken") or 0)
    potential = int(summary.get("potentially_broken") or 0)
    if status == "cancelled":
        folded["code"] = "cancelled"
        folded["label"] = "CANCELLED"
        return folded
    if broken or potential:
        folded["code"] = "attention_required"
        folded["label"] = "ATTENTION REQUIRED"
    elif status == "completed" and analysis.get("comparison_mode") == "partial" and folded.get("code") in {"clear", "no_changes"} and (analysis.get("changed_services") or []):
        folded["code"] = "incomplete"
        folded["label"] = "INCOMPLETE"
        folded["summary"] = "Cluster interdependency analysis incomplete"
    child_services = set(folded.get("evidenced_services") or [])
    union = sorted(child_services | set(analysis.get("affected_services") or []))
    folded["evidenced_services"] = union
    folded["evidenced_service_count"] = len(union)
    folded["changed_services"] = list(analysis.get("changed_services") or [])
    folded["unchanged_but_affected_services"] = list(analysis.get("unchanged_but_affected_services") or [])
    why = _why(analysis)
    if why:
        previous = str(folded.get("summary") or "").rstrip(".")
        folded["summary"] = f"{previous}. {why}" if previous else why
    folded["interdependency"] = {
        "broken": broken,
        "potentially_broken": potential,
        "coordinated": int(summary.get("coordinated_change") or 0) + int(summary.get("resolved_by_change_set") or 0),
        "unchanged_but_affected": list(analysis.get("unchanged_but_affected_services") or []),
    }
    return folded


def public_analysis(analysis: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(analysis, dict):
        return None
    return {
        "comparison_mode": analysis.get("comparison_mode") or "",
        "comparison_coverage": analysis.get("comparison_coverage") or {},
        "limitations": list(analysis.get("limitations") or []),
        "diagnostic": analysis.get("diagnostic") or {},
        "summary": analysis.get("summary") or {},
        "items": list(analysis.get("items") or []),
        "changed_services": analysis.get("changed_services") or [],
        "affected_services": analysis.get("affected_services") or [],
        "unchanged_but_affected_services": analysis.get("unchanged_but_affected_services") or [],
        "temporal_context": analysis.get("temporal_context") or [],
        "change_set": analysis.get("change_set") or {},
        "cluster_evidence": list(analysis.get("cluster_evidence") or []),
    }


def copy_summary(record: dict[str, Any]) -> str:
    """Short clipboard text. Same facts as the report opening, without the section body."""
    analysis = record.get("interdependencies") or {}
    summary = analysis.get("summary") or {}
    assessment = record.get("assessment") or {}
    cluster = record.get("cluster_label") or record.get("cluster_id") or "Cluster"
    label = assessment.get("label") or "Incomplete"
    outstanding = analysis.get("unchanged_but_affected_services") or []
    parts = [
        f"{cluster}: {label}",
        f"{int(summary.get('broken') or 0)} broken",
        f"{int(summary.get('potentially_broken') or 0)} potentially breaking",
        f"{int(summary.get('coordinated_change') or 0)} coordinated",
    ]
    if outstanding:
        parts.append("unchanged but affected: " + ", ".join(outstanding))
    return ". ".join(parts) + "."


def render_markdown(record: dict[str, Any]) -> str:
    """Cluster review in the same section order as a Boundary Gate review."""
    analysis = record.get("interdependencies") or {}
    summary = analysis.get("summary") or {}
    assessment = record.get("assessment") or {}
    source = record.get("source") or {}
    counters = record.get("counters") or {}
    changed = [item for item in analysis.get("items") or [] if item.get("classification") != "unchanged"]
    lines = [
        "# Nami Cluster Change Review",
        "",
        f"Assessment: {assessment.get('label') or 'Incomplete'}",
        "",
        assessment.get("summary") or "No cluster conclusion was recorded.",
        "",
        f"Cluster: {record.get('cluster_label') or record.get('cluster_id') or ''}",
        f"Source: {_source_label(source)}",
    ]
    if source.get("since") or source.get("until"):
        lines.append(f"Timeframe: {source.get('since') or ''} → {source.get('until') or ''}")
    lines.extend([
        f"Comparison: {_mode_label(analysis.get('comparison_mode') or 'evidence_derived')}",
        _coverage_line(analysis),
        "",
        "This is a cluster assessment. It is not a Boundary Gate decision. Repository Gate results are unchanged.",
        "",
        "## Change summary",
        "",
        f"{int(counters.get('repositories_inspected') or 0)} repositories inspected. {int(counters.get('repositories_with_changes') or 0)} changed. {int(counters.get('repositories_no_change') or 0)} had no changes.",
        f"Commits analysed: {_commit_count(record)}.",
        "",
        "## What needs attention",
        "",
    ])
    actions = _actions_from(changed)
    lines.extend([f"- {item}" for item in actions] or ["Nothing further is required from the relationship comparison."])
    lines.extend(["", "## Interdependency changes", "", "| Result | Count |", "| --- | ---: |"])
    for key, label in (
        ("examined", "Relationships examined"),
        ("broken", "Broken"),
        ("potentially_broken", "Potentially breaking"),
        ("modified", "Modified"),
        ("introduced", "Introduced"),
        ("relationship_removed", "Removed"),
        ("coordinated_change", "Coordinated"),
        ("resolved_by_change_set", "Resolved by change set"),
        ("unchanged", "Unchanged"),
    ):
        lines.append(f"| {label} | {int(summary.get(key) or 0)} |")
    lines.append("")
    if not changed:
        lines.append("No relationship changed.")
        lines.append("")
    for item in changed:
        title = str(item.get("classification") or "").replace("_", " ").capitalize()
        lines.extend([
            f"### {title}",
            "",
            f"Provider: {item.get('provider_service') or item.get('source_service') or ''}",
            f"Contract: {item.get('contract_id') or ''}",
            f"Consumer: {item.get('consumer_service') or item.get('target_service') or ''}",
            f"Before: {_presence(item.get('before') or {})}",
            f"After: {_presence(item.get('after') or {})}",
            "",
            item.get("statement") or "",
            "",
        ])
        if item.get("required_action"):
            lines.extend([f"Action: {item.get('required_action')}", ""])
        evidence = ", ".join(item.get("evidence_ids") or [])
        qualified = _qualified_refs(item.get("child_evidence_refs") or [])
        if evidence or qualified:
            lines.extend([f"Evidence: {', '.join(part for part in (evidence, qualified) if part)}", ""])
        path = " → ".join(f"{step.get('service_id')}" for step in item.get("path") or [] if step.get("service_id"))
        if path:
            lines.extend([f"Path: {path}", ""])
    outstanding = analysis.get("unchanged_but_affected_services") or []
    lines.extend(["## Cross-repository impact", ""])
    if outstanding:
        lines.append("Unchanged but affected: " + ", ".join(outstanding) + ".")
    elif analysis.get("affected_services"):
        lines.append("Affected: " + ", ".join(analysis.get("affected_services") or []) + ".")
    else:
        lines.append("No additional service was affected.")
    lines.append("")
    members = list(record.get("repositories") or [])
    lines.extend(["## Blocking findings", ""])
    lines.extend(_member_lines(members, "block") or ["No repository returned BLOCK."])
    lines.extend(["", "## Warnings", ""])
    lines.extend(_member_lines(members, "warn") or ["No repository returned WARN."])
    lines.extend(["", "## Review targets", ""])
    lines.extend(_member_lines(members, "incomplete") or ["No repository review is incomplete."])
    lines.extend(["", "## Repository outcomes", "", "| Service | Result | Review |", "| --- | --- | --- |"])
    for member in members:
        lines.append(f"| {_cell(member.get('service_id'))} | {_cell(_member_result(member))} | {_cell(member.get('review_id') or '')} |")
    lines.extend(["", "## Verification", ""])
    verified = [item for item in members if item.get("validation_status")]
    lines.extend([f"- {item.get('service_id')}: {item.get('validation_status')}" for item in verified] or ["No repository validation ran."])
    lines.extend([
        "",
        "## Assessment",
        "",
        assessment.get("label") or "Incomplete",
        "",
        assessment.get("summary") or "",
        "",
        "## Actions",
        "",
    ])
    lines.extend([f"- {item}" for item in actions] or ["No relationship action was recorded."])
    lines.extend(["", "## Evidence", ""])
    evidence_rows = [(item.get("evidence_ids") or [], item.get("statement") or "") for item in changed]
    if not evidence_rows:
        lines.append("No relationship evidence was recorded.")
    for ids, statement in evidence_rows:
        lines.append(f"- {', '.join(ids) or 'CEV'}: {statement}")
    lines.extend([
        "",
        "## Validation / limitations",
        "",
        f"- Comparison: {_mode_label(analysis.get('comparison_mode') or 'evidence_derived')}.",
        *([f"- {note}" for note in analysis.get("limitations") or []]),
        "- A historical co-change does not prove a dependency and cannot mark a relationship broken.",
        "- Repository Gate decisions were not rewritten.",
        "",
    ])
    diagnostic = analysis.get("diagnostic") or {}
    if diagnostic:
        lines.extend([
            f"- Cluster interdependency analysis incomplete ({diagnostic.get('code') or 'analysis_incomplete'}; stage {diagnostic.get('stage') or 'unknown'}).",
            "",
        ])
    return "\n".join(lines)


def _source_label(source: dict[str, Any]) -> str:
    labels = {
        "working_tree": "Working tree",
        "staged": "Staged",
        "commit": "Commit",
        "range": "Commit range",
        "timeframe": "Timeframe",
        "github_pr": "Pull request",
    }
    return labels.get(str(source.get("kind") or ""), str(source.get("kind") or ""))


def _mode_label(mode: str) -> str:
    return {
        "exact_boundary_snapshots": "Exact boundary dependency comparison",
        "evidence_derived": "Evidence-derived change-set comparison",
        "partial": "Partial boundary comparison",
    }.get(mode, mode)


def _commit_count(record: dict[str, Any]) -> int:
    return sum(int(item.get("commit_count") or 0) for item in record.get("repositories") or [])


def _actions_from(items: list[dict[str, Any]]) -> list[str]:
    actions = []
    for item in items:
        action = str(item.get("required_action") or "").strip()
        if not action:
            continue
        line = f"{item.get('target_service') or item.get('source_service') or 'Service'}: {action}"
        if line not in actions:
            actions.append(line)
    return actions


def _member_lines(members: list[dict[str, Any]], decision: str) -> list[str]:
    return [f"- {item.get('service_id')}: {decision.upper()}. Review {item.get('review_id') or 'not stored'}." for item in members if item.get("decision") == decision]


def _member_result(member: dict[str, Any]) -> str:
    if member.get("status") == "no_change":
        return "No changes"
    if member.get("status") == "failed":
        return "Failed"
    if member.get("status") == "cancelled":
        return "Cancelled"
    decision = str(member.get("decision") or "")
    return decision.upper() if decision else str(member.get("status") or "")


def _cell(value: Any) -> str:
    return str(value or "").replace("|", "/")


def render_json(record: dict[str, Any]) -> dict[str, Any]:
    analysis = record.get("interdependencies") or {}
    return {
        "schema": "nami.cluster.change_review",
        "cluster_review_id": record.get("cluster_review_id") or "",
        "cluster_id": record.get("cluster_id") or "",
        "cluster_label": record.get("cluster_label") or "",
        "source": record.get("source") or {},
        "comparison_mode": analysis.get("comparison_mode") or "",
        "comparison_coverage": analysis.get("comparison_coverage") or {},
        "assessment": record.get("assessment") or {},
        "summary": analysis.get("summary") or {},
        "changed_services": analysis.get("changed_services") or [],
        "affected_services": analysis.get("affected_services") or [],
        "unchanged_but_affected_services": analysis.get("unchanged_but_affected_services") or [],
        "change_set": analysis.get("change_set") or {},
        "interdependencies": analysis.get("items") or [],
        "dependency_paths": analysis.get("dependency_paths") or [],
        "cluster_evidence": analysis.get("cluster_evidence") or [],
        "temporal_context": analysis.get("temporal_context") or [],
        "diagnostics": (analysis.get("diagnostic") or None) and [analysis.get("diagnostic")] or [],
        "limitations": analysis.get("limitations") or [],
        "repositories": [
            {
                "service_id": item.get("service_id") or "",
                "status": item.get("status") or "",
                "decision": item.get("decision") or "",
                "review_id": item.get("review_id") or "",
                "changed": bool(item.get("change_detected")),
            }
            for item in record.get("repositories") or []
        ],
    }


def render_pdf(record: dict[str, Any]) -> bytes:
    from dep_intel.review_pdf import render_text_pdf

    return render_text_pdf(render_markdown(record).splitlines())


def evidence_zip(record: dict[str, Any]) -> bytes:
    from dep_intel.path_hygiene import scrub_value

    analysis = record.get("interdependencies") or {}
    payload = render_json(record)
    files = {
        "cluster-review.json": payload,
        "change-set.json": analysis.get("change_set") or {},
        "before-state.json": analysis.get("before_state") or {},
        "after-state.json": analysis.get("after_state") or {},
        "interdependencies.json": analysis.get("items") or [],
        "cluster-evidence.json": analysis.get("cluster_evidence") or [],
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, body in files.items():
            archive.writestr(name, json.dumps(scrub_value(body), indent=2, sort_keys=True))
        archive.writestr("cluster-review.md", render_markdown(record))
    return buffer.getvalue()


def boundary_refs(repo: Path, since: str, until: str) -> dict[str, str]:
    """Latest commit before the window, and latest commit at the end of the window."""
    from dep_intel.commit_window import window_bounds
    from dep_intel.review_sources import run_git

    try:
        start, end = window_bounds(since, until)
    except ValueError:
        return {"before_ref": "", "after_ref": ""}
    before = _rev_before(repo, start.isoformat(), run_git)
    after = _rev_before(repo, end.isoformat(), run_git)
    return {"before_ref": before, "after_ref": after}


def _rev_before(repo: Path, stamp: str, run_git) -> str:
    if not repo.is_dir():
        return ""
    code, stdout, _stderr = run_git(repo, ["rev-list", "-1", f"--before={stamp}", "HEAD"], timeout=8)
    if code != 0:
        return ""
    return stdout.strip()


def _actions(members: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped = {"provider_removed": [], "provider_added": [], "provider_modified": [], "consumer_removed": [], "consumer_added": []}
    for member in members:
        if not member.get("changed"):
            continue
        service = str(member.get("service_id") or "")
        review_id = str(member.get("review_id") or "")
        for change in member.get("contract_changes") or []:
            if not isinstance(change, dict) or change.get("temporal_only"):
                continue
            contract = _contract_key(change)
            if not contract or not service:
                continue
            kind = str(change.get("kind") or "")
            role = str(change.get("role") or "")
            evidence = [str(item) for item in change.get("evidence_ids") or [] if item]
            consumers = []
            for consumer in change.get("consumers") or []:
                name = consumer.get("service_id") if isinstance(consumer, dict) else consumer
                if name:
                    consumers.append(str(name))
            row = {
                "service_id": service,
                "contract": contract,
                "provider": str(change.get("provider") or ""),
                "replacement": _contract_key({"contract": change.get("replacement"), "method": change.get("replacement_method"), "path": change.get("replacement_path")}) if change.get("replacement") or change.get("replacement_path") else "",
                "review_id": review_id,
                "evidence_ids": evidence,
                "kind": kind,
                "breaking": bool(change.get("breaking")),
                "consumers": consumers,
            }
            if kind in {"consumption_removed", "consumer_removed"} or role == "consumer" and "remov" in kind:
                grouped["consumer_removed"].append(row)
            elif kind in {"consumption_added", "consumer_added"} or role == "consumer" and "add" in kind:
                grouped["consumer_added"].append(row)
            elif kind in {"operation_removed", "removed_operation"} or (change.get("breaking") and "remov" in kind):
                grouped["provider_removed"].append(row)
            elif kind in {"operation_added", "added_operation"}:
                grouped["provider_added"].append(row)
            elif kind in {"handler_changed", "required_request_field_added", "response_field_removed", "unknown_schema_change"}:
                grouped["provider_modified"].append(row)
    return grouped


def _edges(relationships: list[dict[str, Any]], actions: dict[str, list[dict[str, Any]]]) -> list[dict[str, str]]:
    edges = []
    for item in relationships:
        consumer = str(item.get("from_service") or item.get("consumer") or "")
        provider = str(item.get("to_service") or item.get("provider") or "")
        contract = _contract_key(item)
        if consumer and provider and contract and consumer != provider:
            edges.append({
                "consumer": consumer,
                "provider": provider,
                "contract": contract,
                "kind": str(item.get("kind") or item.get("relationship") or "http"),
                "provenance": str(item.get("provenance") or "confirmed"),
            })
    for removed in actions.get("provider_removed") or []:
        for consumer in removed.get("consumers") or []:
            edges.append({"consumer": consumer, "provider": removed["service_id"], "contract": removed["contract"], "kind": "http", "provenance": "confirmed"})
    return edges


def _classify(edge: dict[str, str], actions: dict[str, list[dict[str, Any]]], relationships: list[dict[str, Any]]) -> dict[str, Any]:
    consumer, provider, contract = edge["consumer"], edge["provider"], edge["contract"]
    removed = _match(actions["provider_removed"], provider, contract)
    consumer_stopped = _consumer_match(actions["consumer_removed"], consumer, provider, contract)
    replacement = _replacement(actions, provider, consumer, contract)
    modified = _match(actions["provider_modified"], provider, contract)
    verified = _verified(edge)
    if removed and consumer_stopped and replacement:
        classification = "coordinated_change"
        statement = f"{consumer} moved from {contract} to {replacement}."
        action = ""
    elif removed and consumer_stopped:
        classification = "relationship_removed"
        statement = f"Both sides dropped {contract}."
        action = ""
    elif removed and not consumer_stopped:
        classification = "broken" if verified else "potentially_broken"
        statement = (
            f"{provider} removed {contract}. {consumer} still uses it."
            if classification == "broken"
            else f"{provider} changed {contract}. {consumer} was not verified."
        )
        action = "Migrate the consumer or restore the contract."
    elif modified and not consumer_stopped:
        classification, statement, action = _modification_result(modified, provider, consumer, contract, verified)
    else:
        classification = "unchanged"
        statement = f"{consumer} still depends on {provider} {contract}."
        action = ""
    paths = _impact_paths(relationships, provider, consumer, contract)
    path = [{"service_id": service_id, "role": "provider" if service_id == provider else "consumer", "contract": contract if service_id in {provider, consumer} else ""} for service_id in (paths[0] if paths else [provider, consumer])]
    refs = _child_refs(removed, consumer_stopped, modified)
    reviews = [item for item in {(removed or {}).get("review_id", ""), (consumer_stopped or {}).get("review_id", ""), (modified or {}).get("review_id", "")} if item]
    affected = [consumer] if classification in {"broken", "potentially_broken", "modified"} else [provider, consumer]
    changed = [service for service, row in ((provider, removed or modified), (consumer, consumer_stopped)) if row]
    return _item(
        provider=provider,
        consumer=consumer,
        contract=contract,
        kind=edge["kind"],
        classification=classification,
        statement=statement,
        action=action,
        evidence=[],
        reviews=reviews,
        changed=changed,
        affected=affected,
        path=path,
        paths=[[step["service_id"] for step in path] for _ in [0]] if not paths else paths,
        child_refs=refs,
        before={"provider_present": True, "consumer_reference_present": True},
        after={
            "provider_present": classification in {"unchanged", "modified"},
            "consumer_reference_present": classification in {"broken", "potentially_broken", "unchanged", "modified"},
        },
        before_exists=True,
        after_exists=classification in {"unchanged", "modified"},
    )


def _introduced(actions: dict[str, list[dict[str, Any]]], seen: set[tuple[str, str, str]]) -> list[dict[str, Any]]:
    created = []
    for added in actions["consumer_added"]:
        provider = added["provider"] or ""
        consumer = added["service_id"]
        contract = added["contract"]
        if not provider or (consumer, provider, contract) in seen:
            continue
        provider_added = _match(actions["provider_added"], provider, contract)
        if _replaced_pair(actions, provider, consumer, contract):
            classification = "coordinated_change"
            statement = f"{consumer} moved to {contract}."
        else:
            classification = "introduced"
            statement = f"New dependency: {consumer} → {provider} {contract}."
        created.append(_item(
            provider=provider,
            consumer=consumer,
            contract=contract,
            kind="http",
            classification=classification,
            statement=statement,
            action="",
            evidence=[],
            reviews=[item for item in {added["review_id"], (provider_added or {}).get("review_id", "")} if item],
            changed=[consumer, provider] if provider_added else [consumer],
            affected=[consumer, provider],
            path=[{"service_id": provider, "role": "provider", "contract": contract}, {"service_id": consumer, "role": "consumer", "contract": contract}],
            child_refs=_child_refs(added, provider_added),
            before_exists=False,
            after_exists=True,
        ))
        seen.add((consumer, provider, contract))
    return created


def _replaced_pair(actions: dict[str, list[dict[str, Any]]], provider: str, consumer: str, contract: str) -> bool:
    return any(item["service_id"] == provider and item["contract"] != contract for item in actions["provider_removed"]) and any(
        item["service_id"] == consumer and (not item["provider"] or item["provider"] == provider) and item["contract"] != contract for item in actions["consumer_removed"]
    )


def _replacement(actions: dict[str, list[dict[str, Any]]], provider: str, consumer: str, contract: str) -> str:
    added = [item["contract"] for item in actions["provider_added"] if item["service_id"] == provider and item["contract"] != contract]
    moved = [item["contract"] for item in actions["consumer_added"] if item["service_id"] == consumer and (not item["provider"] or item["provider"] == provider)]
    shared = [item for item in added if item in moved]
    return shared[0] if shared else ""


def _item(**kwargs: Any) -> dict[str, Any]:
    before = kwargs.get("before") or {"provider_present": bool(kwargs["before_exists"]), "consumer_reference_present": bool(kwargs["before_exists"])}
    after = kwargs.get("after") or {"provider_present": bool(kwargs["after_exists"]), "consumer_reference_present": bool(kwargs["after_exists"])}
    before = {**before, "exists": bool(before.get("provider_present") and before.get("consumer_reference_present"))}
    after = {**after, "exists": bool(after.get("provider_present") and after.get("consumer_reference_present"))}
    path = list(kwargs["path"])
    paths = list(kwargs.get("paths") or ([path] if path else []))
    refs = list(kwargs.get("child_refs") or [])
    return {
        "provider_service": kwargs["provider"],
        "consumer_service": kwargs["consumer"],
        # source_service is the provider and target_service is the consumer.
        # Raw graph rows use the opposite direction: from_service is the consumer.
        "source_service": kwargs["provider"],
        "target_service": kwargs["consumer"],
        "relationship": _relationship_name(kwargs["kind"]),
        "contract_id": kwargs["contract"],
        "before": before,
        "after": after,
        "classification": kwargs["classification"],
        "changed_services": kwargs["changed"],
        "affected_services": kwargs["affected"],
        "evidence_ids": list(kwargs["evidence"]),
        "child_evidence_refs": refs,
        "child_review_ids": kwargs["reviews"],
        "path": path,
        "paths": paths,
        "path_truncated": bool(kwargs.get("truncated")),
        "truncation_reason": kwargs.get("truncation_reason") or "",
        "statement": kwargs["statement"],
        "required_action": kwargs["action"],
        "old_contract": kwargs.get("old_contract") or "",
        "new_contract": kwargs.get("new_contract") or "",
        "pairing_reason": kwargs.get("pairing_reason") or "",
        "migration_status": kwargs.get("migration_status") or "",
        "migrated_consumers": list(kwargs.get("migrated_consumers") or []),
        "outstanding_consumers": list(kwargs.get("outstanding_consumers") or []),
        "provider_changed": bool(kwargs.get("provider_changed")),
        "consumer_changed": bool(kwargs.get("consumer_changed")),
    }


def _summary(items: list[dict[str, Any]]) -> dict[str, int]:
    counts = {key: 0 for key in CLASS_ORDER}
    counts["examined"] = len(items)
    for item in items:
        key = str(item.get("classification") or "")
        if key in counts:
            counts[key] += 1
    return counts


def _why(analysis: dict[str, Any]) -> str:
    summary = analysis.get("summary") or {}
    broken = int(summary.get("broken") or 0)
    potential = int(summary.get("potentially_broken") or 0)
    coordinated = int(summary.get("coordinated_change") or 0)
    outstanding = analysis.get("unchanged_but_affected_services") or []
    parts = []
    if broken:
        parts.append(f"{broken} unresolved contract break{'s' if broken != 1 else ''}")
    if potential:
        parts.append(f"{potential} potentially broken relationship{'s' if potential != 1 else ''}")
    if coordinated:
        parts.append(f"{coordinated} coordinated change{'s' if coordinated != 1 else ''}")
    if outstanding:
        parts.append("Unchanged affected services: " + ", ".join(outstanding))
    if parts:
        return ". ".join(parts) + "."
    return ""


def _change_row(member: dict[str, Any]) -> dict[str, Any]:
    boundary = member.get("_boundary") if isinstance(member.get("_boundary"), dict) else {}
    return {
        "service_id": member.get("service_id") or "",
        "changed": bool(member.get("changed") or member.get("change_detected")),
        "review_id": member.get("review_id") or "",
        "decision": member.get("decision") or "",
        "before_ref": member.get("before_ref") or boundary.get("before_ref") or "",
        "after_ref": member.get("after_ref") or boundary.get("after_ref") or "",
        "changed_file_count": int(member.get("changed_file_count") or 0),
        "commit_count": int(member.get("commit_count") or 0),
        "contract_changes": member.get("contract_changes") or [],
        "state_status": member.get("state_status") or boundary.get("state_status") or "",
    }


def _classify_boundaries(
    before_edges: list[dict[str, str]],
    after_edges: list[dict[str, str]],
    actions: dict[str, list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], set[tuple[str, str, str]]]:
    before = {(item["consumer"], item["provider"], item["contract"]): item for item in before_edges}
    after = {(item["consumer"], item["provider"], item["contract"]): item for item in after_edges}
    items: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for key, edge in before.items():
        consumer, provider, contract = key
        still = key in after
        replacement = _boundary_replacement(before, after, provider, consumer, contract)
        removed = _match(actions["provider_removed"], provider, contract)
        modified = _match(actions["provider_modified"], provider, contract)
        if not still and replacement:
            classification = "coordinated_change"
            statement = f"{consumer} moved from {contract} to {replacement}."
            action = ""
        elif not still:
            classification = "relationship_removed"
            statement = f"Both sides dropped {contract}."
            action = ""
        elif removed:
            classification = "broken" if _verified(edge) else "potentially_broken"
            statement = f"{provider} removed {contract}. {consumer} still uses it."
            action = "Migrate the consumer or restore the contract."
        elif modified:
            classification = "modified"
            statement = f"{contract} changed. No break established."
            action = ""
        else:
            classification = "unchanged"
            statement = f"{consumer} still depends on {provider} {contract}."
            action = ""
        items.append(_item(
            provider=provider,
            consumer=consumer,
            contract=contract,
            kind=edge["kind"],
            classification=classification,
            statement=statement,
            action=action,
            evidence=list((removed or {}).get("evidence_ids") or []) + list((modified or {}).get("evidence_ids") or []),
            reviews=[item for item in {(removed or {}).get("review_id", ""), (modified or {}).get("review_id", "")} if item],
            changed=[provider] if removed or modified else [],
            affected=[consumer] if classification in {"broken", "potentially_broken", "modified"} else [provider, consumer],
            path=[{"service_id": provider, "role": "provider", "contract": contract}, {"service_id": consumer, "role": "consumer", "contract": contract}],
            before_exists=True,
            after_exists=still or classification == "coordinated_change",
        ))
        seen.add(key)
    for key, edge in after.items():
        if key in before:
            continue
        consumer, provider, contract = key
        if _boundary_replacement(before, after, provider, consumer, contract):
            classification = "coordinated_change"
            statement = f"{consumer} moved to {contract}."
        else:
            classification = "introduced"
            statement = f"New dependency: {consumer} → {provider} {contract}."
        items.append(_item(
            provider=provider,
            consumer=consumer,
            contract=contract,
            kind=edge["kind"],
            classification=classification,
            statement=statement,
            action="",
            evidence=[],
            reviews=[],
            changed=[provider, consumer],
            affected=[provider, consumer],
            path=[{"service_id": consumer, "role": "consumer", "contract": contract}, {"service_id": provider, "role": "provider", "contract": contract}],
            before_exists=False,
            after_exists=True,
        ))
        seen.add(key)
    return items, seen


def _boundary_replacement(before: dict, after: dict, provider: str, consumer: str, contract: str) -> str:
    removed = any(key[1] == provider and key[2] == contract and key[0] == consumer for key in before)
    if not removed:
        return ""
    for key in after:
        if key[0] == consumer and key[1] == provider and key[2] != contract and (key[0], key[1], contract) not in after:
            return key[2]
    return ""


def _verified(edge: dict[str, Any]) -> bool:
    provenance = str(edge.get("provenance") or "confirmed").strip().lower()
    if provenance in TEMPORAL:
        return False
    if not provenance or provenance in {item.lower() for item in STRUCTURAL}:
        return True
    return False


def _relationship_name(kind: str) -> str:
    names = {
        "http": "http_contract",
        "http_contract": "http_contract",
        "grpc": "grpc_contract",
        "grpc_contract": "grpc_contract",
        "event": "event_contract",
        "event_contract": "event_contract",
    }
    if kind in names:
        return names[kind]
    if kind and kind.endswith("_contract"):
        return kind
    return kind or "unknown"


def _temporal(item: dict[str, Any]) -> bool:
    provenance = str(item.get("provenance") or item.get("evidence") or "").lower()
    return bool(item.get("temporal_only")) or provenance in TEMPORAL


def _temporal_note(item: dict[str, Any]) -> dict[str, str]:
    return {
        "from_service": str(item.get("from_service") or ""),
        "to_service": str(item.get("to_service") or ""),
        "contract": _contract_key(item),
        "statement": "Historical association only. Not a verified dependency.",
    }


def _contract_key(item: dict[str, Any]) -> str:
    from dep_intel.cluster_dependency_state import canonical_contract_identity

    return canonical_contract_identity(item)


def _match(rows: list[dict[str, Any]], service: str, contract: str) -> dict[str, Any] | None:
    for item in rows:
        if item["service_id"] == service and item["contract"] == contract:
            return item
    return None


def _consumer_match(rows: list[dict[str, Any]], consumer: str, provider: str, contract: str) -> dict[str, Any] | None:
    for item in rows:
        if item["service_id"] == consumer and item["contract"] == contract and (not item["provider"] or item["provider"] == provider):
            return item
    return None


def _downstream(relationships: list[dict[str, Any]], consumer: str, provider: str) -> str:
    """First downstream consumer of ``consumer``.

    Confirmed graph rows point consumer → provider (from_service → to_service).
    Impact walks the other way, so the next service is a caller of ``consumer``.
    """
    paths = _impact_paths(relationships, provider, consumer, "")
    if not paths:
        return ""
    first = paths[0]
    if len(first) > 2:
        return first[2]
    return ""


_IRRECONCILABLE = {"BG-VERIFY-001", "BG-POLICY-001", "BG-CRITICAL-001", "BG-GRAPH-001", "BG-EVIDENCE-001"}
_BREAKING_KINDS = {"required_request_field_added", "response_field_removed"}
_MAX_PATHS = 16
_MAX_DEPTH = 8


def _modification_result(modified: dict[str, Any], provider: str, consumer: str, contract: str, verified: bool) -> tuple[str, str, str]:
    kind = str(modified.get("kind") or "")
    if kind == "unknown_schema_change":
        return (
            "potentially_broken",
            f"{provider} changed {contract}, but schema compatibility is not proven. {consumer} still uses it.",
            "Confirm whether the consumer is compatible with the changed contract.",
        )
    if kind in _BREAKING_KINDS or (modified.get("breaking") and kind != "handler_changed"):
        classification = "broken" if verified else "potentially_broken"
        return (
            classification,
            f"{provider} changed {contract} in a breaking way. {consumer} still uses it.",
            "Migrate the consumer or restore a compatible provider contract.",
        )
    return "modified", f"{contract} changed. No break established.", ""


def _classify_states(before: dict[str, Any], after: dict[str, Any], actions: dict[str, list[dict[str, Any]]], members: list[dict[str, Any]], mode: str) -> list[dict[str, Any]]:
    return _classify_facts(
        _state_facts(before),
        _state_facts(after),
        actions,
        members,
        mode,
        list(before.get("relationships") or []) + list(after.get("relationships") or []),
        _provider_index(before),
        _provider_index(after),
    )


def _state_facts(state: dict[str, Any]) -> dict[tuple[str, str, str, str], dict[str, Any]]:
    facts = {}
    for item in state.get("relationships") or []:
        if not isinstance(item, dict) or _temporal(item):
            continue
        identity = _contract_key(item) or str(item.get("contract_identity") or "")
        provider = str(item.get("provider_service") or "")
        consumer = str(item.get("consumer_service") or "")
        kind = _relationship_name(str(item.get("relationship_type") or item.get("kind") or "http"))
        if not provider or not consumer or not identity:
            continue
        facts[(kind, provider, consumer, identity)] = {
            "provider_present": bool(item.get("provider_present")),
            "consumer_reference_present": bool(item.get("consumer_reference_present")),
            "confirmed": bool(item.get("confirmed")),
            "provenance": str(item.get("provenance") or "source_extraction"),
            "kind": kind,
            "provider_file": item.get("provider_file") or "",
            "provider_line": int(item.get("provider_line") or 0),
            "consumer_file": item.get("consumer_file") or "",
            "consumer_line": int(item.get("consumer_line") or 0),
            "before_ref": "",
            "after_ref": "",
        }
    return facts


def _facts_from_edges(edges: list[dict[str, Any]]) -> dict[tuple[str, str, str, str], dict[str, Any]]:
    facts = {}
    for edge in _edges(edges, {"provider_removed": []}):
        kind = _relationship_name(edge["kind"])
        facts[(kind, edge["provider"], edge["consumer"], edge["contract"])] = {
            "provider_present": True,
            "consumer_reference_present": True,
            "confirmed": True,
            "provenance": edge["provenance"],
            "kind": kind,
            "provider_file": "",
            "provider_line": 0,
            "consumer_file": "",
            "consumer_line": 0,
        }
    return facts


def _provider_index(state: dict[str, Any]) -> set[tuple[str, str]]:
    return {
        (str(item.get("service_id") or ""), str(item.get("contract_identity") or ""))
        for item in state.get("provider_contracts") or []
        if isinstance(item, dict)
    }


def _classify_facts(
    before: dict[tuple[str, str, str, str], dict[str, Any]],
    after: dict[tuple[str, str, str, str], dict[str, Any]],
    actions: dict[str, list[dict[str, Any]]],
    members: list[dict[str, Any]],
    mode: str,
    relationships: list[dict[str, Any]],
    before_providers: set[tuple[str, str]] | None = None,
    after_providers: set[tuple[str, str]] | None = None,
) -> list[dict[str, Any]]:
    items = []
    keys = sorted(set(before) | set(after))
    partial = mode == "partial"
    before_providers = before_providers or set()
    after_providers = after_providers or set()
    changed_ids = {str(item.get("service_id") or "") for item in members if item.get("changed")}
    for key in keys:
        kind, provider, consumer, contract = key
        left = dict(before.get(key) or _absent())
        right = dict(after.get(key) or _absent())
        if key not in before:
            left["provider_present"] = (provider, contract) in before_providers
        if key not in after:
            right["provider_present"] = (provider, contract) in after_providers
        if _temporal(left) or _temporal(right):
            continue
        if not any((left["provider_present"], left["consumer_reference_present"], right["provider_present"], right["consumer_reference_present"])):
            continue
        verified = (not partial) and _verified(left) and _verified(right if right["provider_present"] or right["consumer_reference_present"] else left)
        removed = _match(actions["provider_removed"], provider, contract)
        modified = _match(actions["provider_modified"], provider, contract)
        classification, statement, action = _fact_class(left, right, provider, consumer, contract, verified, modified)
        changed = [service for service in (provider, consumer) if service in changed_ids]
        affected = sorted({provider, consumer}) if classification != "unchanged" else [consumer]
        if classification == "unchanged":
            affected = [provider, consumer]
        paths = _impact_paths(relationships, provider, consumer, contract)
        path = [{"service_id": service_id, "role": "provider" if service_id == provider else "consumer", "contract": contract if service_id in {provider, consumer} else ""} for service_id in (paths[0] if paths else [provider, consumer])]
        items.append(_item(
            provider=provider,
            consumer=consumer,
            contract=contract,
            kind=kind,
            classification=classification,
            statement=statement,
            action=action,
            evidence=[],
            reviews=[row["review_id"] for row in (removed, modified) if row and row.get("review_id")],
            changed=changed,
            affected=affected if classification != "unchanged" else [provider, consumer],
            path=path,
            paths=paths or [[provider, consumer]],
            child_refs=_child_refs(removed, modified, _consumer_match(actions["consumer_removed"], consumer, provider, contract), _consumer_match(actions["consumer_added"], consumer, provider, contract)),
            before=dict(left),
            after=dict(right),
            before_exists=left["provider_present"] and left["consumer_reference_present"],
            after_exists=right["provider_present"] and right["consumer_reference_present"],
            provider_changed=provider in changed,
            consumer_changed=consumer in changed,
        ))
        items[-1]["_key"] = key
        items[-1]["_left"] = left
        items[-1]["_right"] = right
    _apply_migrations(items, actions)
    _apply_resolution(items, members, mode)
    for item in items:
        item.pop("_key", None)
        item.pop("_left", None)
        item.pop("_right", None)
    return items


def _fact_class(left: dict[str, Any], right: dict[str, Any], provider: str, consumer: str, contract: str, verified: bool, modified: dict[str, Any] | None) -> tuple[str, str, str]:
    provider_before = bool(left["provider_present"])
    provider_after = bool(right["provider_present"])
    consumer_before = bool(left["consumer_reference_present"])
    consumer_after = bool(right["consumer_reference_present"])
    if provider_before and consumer_before and not provider_after and consumer_after:
        classification = "broken" if verified else "potentially_broken"
        return classification, f"{provider} removed {contract}, but {consumer} still uses it.", "Migrate the consumer or restore a compatible provider contract."
    if not consumer_before and consumer_after and not provider_after:
        classification = "broken" if verified else "potentially_broken"
        return classification, f"{consumer} references {contract}, but {provider} does not provide it.", "Add the provider contract or remove the consumer reference."
    if consumer_before and not consumer_after:
        return "relationship_removed", f"{consumer} stopped using {contract}.", ""
    if not consumer_before and consumer_after and provider_after:
        return "introduced", f"New dependency introduced: {consumer} → {provider} {contract}.", ""
    if provider_before and consumer_before and provider_after and consumer_after:
        if modified:
            return _modification_result(modified, provider, consumer, contract, verified)
        return "unchanged", f"{consumer} still depends on {provider} {contract}.", ""
    if not consumer_before and not consumer_after:
        return "unchanged", f"No consumer reference for {contract}.", ""
    return "potentially_broken", f"{provider} {contract} changed for {consumer}, but the boundary is incomplete.", "Confirm the provider and consumer states."


def _apply_migrations(items: list[dict[str, Any]], actions: dict[str, list[dict[str, Any]]]) -> None:
    from dep_intel.cluster_dependency_state import version_successor

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in items:
        grouped.setdefault((item["provider_service"], item["consumer_service"]), []).append(item)
    for pair, rows in grouped.items():
        provider, consumer = pair
        removed = [item for item in rows if item["classification"] == "relationship_removed"]
        introduced = [item for item in rows if item["classification"] == "introduced"]
        for old in removed:
            candidates = []
            for new in introduced:
                explicit = _explicit_pair(old, new, actions)
                successor = version_successor(old["contract_id"], new["contract_id"])
                old_right = old.get("_right") or {}
                new_left = new.get("_left") or {}
                provider_removed = bool((old.get("_left") or {}).get("provider_present")) and not old_right.get("provider_present")
                provider_added = not new_left.get("provider_present") and bool((new.get("_right") or {}).get("provider_present"))
                if explicit or (successor and provider_removed and provider_added):
                    candidates.append((new, "explicit_replacement" if explicit else "version_successor"))
            if len(candidates) != 1:
                continue
            new, reason = candidates[0]
            old["classification"] = "coordinated_change"
            old["old_contract"] = old["contract_id"]
            old["new_contract"] = new["contract_id"]
            old["pairing_reason"] = reason
            old["statement"] = f"{consumer} moved from {old['contract_id']} to {new['contract_id']}."
            old["required_action"] = ""
            old["provider_changed"] = True
            old["consumer_changed"] = True
            new["old_contract"] = old["contract_id"]
            new["new_contract"] = new["contract_id"]
            new["pairing_reason"] = reason
            new["statement"] = f"Migration target: {consumer} now uses {provider} {new['contract_id']}."
    by_contract: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in items:
        if item["classification"] in {"coordinated_change", "broken", "potentially_broken", "relationship_removed"}:
            by_contract.setdefault((item["provider_service"], item.get("old_contract") or item["contract_id"]), []).append(item)
    for rows in by_contract.values():
        migrated = sorted({item["consumer_service"] for item in rows if item["classification"] == "coordinated_change"})
        outstanding = sorted({item["consumer_service"] for item in rows if item["classification"] in {"broken", "potentially_broken"}})
        status = "incomplete" if migrated and outstanding else "complete" if migrated else ""
        for item in rows:
            item["migrated_consumers"] = migrated
            item["outstanding_consumers"] = outstanding
            item["migration_status"] = status


def _explicit_pair(old: dict[str, Any], new: dict[str, Any], actions: dict[str, list[dict[str, Any]]]) -> bool:
    for row in actions.get("provider_removed") or []:
        if row.get("service_id") == old.get("provider_service") and row.get("contract") == old.get("contract_id") and row.get("replacement") == new.get("contract_id"):
            return True
    for row in actions.get("consumer_removed") or []:
        if row.get("service_id") == old.get("consumer_service") and row.get("contract") == old.get("contract_id") and row.get("replacement") == new.get("contract_id"):
            return True
    return False


def _apply_resolution(items: list[dict[str, Any]], members: list[dict[str, Any]], mode: str) -> None:
    if mode == "partial":
        return
    by_service = {str(item.get("service_id") or ""): item for item in members}
    for item in items:
        if item["classification"] != "coordinated_change":
            continue
        if item.get("outstanding_consumers"):
            continue
        provider = by_service.get(item["provider_service"]) or {}
        reasons = {str(code) for code in provider.get("reason_codes") or []}
        if reasons & _IRRECONCILABLE:
            continue
        contract_related = "BG-CONTRACT-001" in reasons or any(str(code).startswith("BG-CONTRACT") for code in reasons)
        if not contract_related:
            continue
        if str(provider.get("decision") or "") not in {"block", "warn"}:
            continue
        if not provider.get("review_id"):
            continue
        item["classification"] = "resolved_by_change_set"
        item["statement"] = (
            f"The isolated provider review returned {str(provider.get('decision') or '').upper()} because {item['contract_id']} changed. "
            f"The cluster change set shows {item['consumer_service']} migrated. The repository Gate result is preserved."
        )
        item["required_action"] = ""


def _assign_evidence(items: list[dict[str, Any]], members: list[dict[str, Any]]) -> None:
    ordered = sorted(
        [item for item in items if item.get("classification") != "unchanged"],
        key=lambda item: (item.get("relationship") or "", item.get("provider_service") or "", item.get("consumer_service") or "", item.get("contract_id") or "", item.get("classification") or ""),
    )
    refs_by_service = {}
    for member in members:
        review_id = str(member.get("review_id") or "")
        if not review_id:
            continue
        refs_by_service.setdefault(str(member.get("service_id") or ""), review_id)
    for index, item in enumerate(ordered, start=1):
        evidence_id = f"CEV-{index:03d}"
        item["evidence_ids"] = [evidence_id]
        before = item.get("before") or {}
        after = item.get("after") or {}
        record = {
            "evidence_id": evidence_id,
            "kind": "dependency_state_delta",
            "relationship_type": item.get("relationship") or "",
            "provider_service": item.get("provider_service") or "",
            "consumer_service": item.get("consumer_service") or "",
            "contract_id": item.get("contract_id") or "",
            "before": {"provider_present": bool(before.get("provider_present")), "consumer_reference_present": bool(before.get("consumer_reference_present"))},
            "after": {"provider_present": bool(after.get("provider_present")), "consumer_reference_present": bool(after.get("consumer_reference_present"))},
            "before_ref": "",
            "after_ref": "",
            "provider_source": {"file": "", "line": 0},
            "consumer_source": {"file": "", "line": 0},
            "provenance": "source_extraction" if item.get("before", {}).get("provider_present") is not None else "child_review",
            "child_evidence_refs": list(item.get("child_evidence_refs") or []),
        }
        item["cluster_evidence"] = record
    for member in members:
        boundary = member.get("_boundary") if isinstance(member.get("_boundary"), dict) else {}
        service_id = str(member.get("service_id") or "")
        for item in ordered:
            record = item.get("cluster_evidence") or {}
            if service_id and service_id == record.get("provider_service"):
                record["before_ref"] = record["before_ref"] or boundary.get("before_ref") or member.get("before_ref") or ""
                record["after_ref"] = record["after_ref"] or boundary.get("after_ref") or member.get("after_ref") or ""


def _impact_paths(relationships: list[dict[str, Any]], provider: str, consumer: str, contract: str) -> list[list[str]]:
    """Provider → consumer → downstream consumers.

    ``from_service`` on a confirmed relationship is the consumer. Paths do not
    follow contracts that the consumer itself calls.
    """
    adjacency: dict[str, list[str]] = {}
    for item in relationships:
        if not isinstance(item, dict) or _temporal(item):
            continue
        if "provider_service" in item or "consumer_service" in item:
            edge_provider = str(item.get("provider_service") or "")
            edge_consumer = str(item.get("consumer_service") or "")
            if item.get("consumer_reference_present") is False:
                continue
        else:
            edge_consumer = str(item.get("from_service") or item.get("consumer") or "")
            edge_provider = str(item.get("to_service") or item.get("provider") or "")
        if not edge_provider or not edge_consumer or edge_provider == edge_consumer:
            continue
        bucket = adjacency.setdefault(edge_provider, [])
        if edge_consumer not in bucket:
            bucket.append(edge_consumer)
    for values in adjacency.values():
        values.sort()
    paths: list[list[str]] = []
    truncated = False
    queue: list[tuple[str, list[str]]] = [(consumer, [provider, consumer])]
    seen = {consumer}
    while queue and len(paths) < _MAX_PATHS:
        current, path = queue.pop(0)
        children = [item for item in adjacency.get(current, []) if item not in path]
        if not children:
            paths.append(path)
            continue
        extended = False
        for child in children:
            if len(path) >= _MAX_DEPTH:
                truncated = True
                paths.append(path)
                extended = True
                break
            queue.append((child, path + [child]))
            extended = True
        if not extended:
            paths.append(path)
    if not paths and provider and consumer:
        paths = [[provider, consumer]]
    if truncated and paths:
        paths = paths[:_MAX_PATHS]
    _ = seen
    _ = contract
    return paths


def _child_refs(*rows: dict[str, Any] | None) -> list[dict[str, str]]:
    refs = []
    seen = set()
    for row in rows:
        if not row:
            continue
        review_id = str(row.get("review_id") or "")
        for evidence_id in row.get("evidence_ids") or []:
            key = (review_id, str(evidence_id))
            if not evidence_id or key in seen:
                continue
            seen.add(key)
            refs.append({"review_id": review_id, "evidence_id": str(evidence_id)})
    return refs


def _absent() -> dict[str, Any]:
    return {"provider_present": False, "consumer_reference_present": False, "confirmed": False, "provenance": "source_extraction", "kind": "http_contract"}


def _states_partial(before: dict[str, Any], after: dict[str, Any]) -> bool:
    for state in (before, after):
        for info in (state.get("services") or {}).values():
            if isinstance(info, dict) and not info.get("exact", True):
                return True
    return False


def _public_state(state: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(state, dict):
        return None
    from dep_intel.path_hygiene import scrub_value

    kept = {
        "state_id": state.get("state_id") or "",
        "state_kind": state.get("state_kind") or "",
        "source_kind": state.get("source_kind") or "",
        "services": state.get("services") or {},
        "provider_contracts": state.get("provider_contracts") or [],
        "consumer_references": state.get("consumer_references") or [],
        "relationships": state.get("relationships") or [],
        "diagnostics": state.get("diagnostics") or [],
        "coverage": state.get("coverage") or {},
    }
    return scrub_value(kept)


def _coverage_line(analysis: dict[str, Any]) -> str:
    coverage = analysis.get("comparison_coverage") or {}
    if not coverage:
        return ""
    partial = coverage.get("services_partial") or []
    total = int(coverage.get("services_total") or 0)
    exact = int(coverage.get("services_exact_before") or coverage.get("services_exact") or 0)
    if analysis.get("comparison_mode") == "partial" and partial:
        return f"{exact} / {total} services reconstructed exactly. Partial services: {', '.join(partial)}."
    types = coverage.get("relationship_types") or {}
    if not types:
        return ""
    return "Relationship coverage: " + ", ".join(f"{kind} {value}" for kind, value in sorted(types.items()))


def _presence(side: dict[str, Any]) -> str:
    provider = "Provider present" if side.get("provider_present") else "Provider absent"
    consumer = "Consumer reference present" if side.get("consumer_reference_present") else "Consumer reference absent"
    return f"{provider} · {consumer}"


def _qualified_refs(refs: list[dict[str, Any]]) -> str:
    parts = []
    for item in refs:
        review_id = str(item.get("review_id") or "")
        evidence_id = str(item.get("evidence_id") or "")
        if not evidence_id:
            continue
        short = review_id[:8] if review_id else "review"
        parts.append(f"Review {short} · {evidence_id}" if review_id else evidence_id)
    return ", ".join(parts)
