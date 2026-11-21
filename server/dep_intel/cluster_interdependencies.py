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

STRUCTURAL = {"extracted", "declared", "openapi", "generated topology", "override", "source extraction", "confirmed", "cluster_graph", "http", "grpc", "event"}
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
) -> dict[str, Any]:
    """Classify relationships between the services in one cluster change set."""
    rels = [item for item in (relationships or []) if isinstance(item, dict)]
    temporal = [item for item in rels if _temporal(item)]
    if before_relationships is not None:
        temporal.extend(item for item in before_relationships if isinstance(item, dict) and _temporal(item))
    actions = _actions(members)
    mode = comparison_mode if comparison_mode in {"exact_boundary_snapshots", "evidence_derived", "partial"} else "evidence_derived"
    if before_relationships is not None and after_relationships is not None:
        mode = "exact_boundary_snapshots" if mode != "partial" else "partial"
        before_edges = _edges([item for item in before_relationships if isinstance(item, dict)], {"provider_removed": []})
        after_edges = _edges([item for item in after_relationships if isinstance(item, dict)], {"provider_removed": []})
        items, seen = _classify_boundaries(before_edges, after_edges, actions)
    else:
        if before_relationships is not None or after_relationships is not None:
            mode = "partial"
        structural = [item for item in rels if not _temporal(item)]
        if not structural and after_relationships is not None:
            structural = [item for item in after_relationships if isinstance(item, dict) and not _temporal(item)]
        edges = _edges(structural, actions)
        items = []
        seen = set()
        for edge in edges:
            key = (edge["consumer"], edge["provider"], edge["contract"])
            if key in seen:
                continue
            seen.add(key)
            items.append(_classify(edge, actions, structural))
        items.extend(_introduced(actions, seen))
    items.sort(key=lambda item: (CLASS_ORDER.index(item["classification"]) if item["classification"] in CLASS_ORDER else 99, item["source_service"], item["contract_id"], item["target_service"]))
    for index, item in enumerate(items, start=1):
        if item["classification"] != "unchanged" and not item["evidence_ids"]:
            item["evidence_ids"] = [f"CEV-{index:03d}"]
    summary = _summary(items)
    changed_services = sorted({str(item.get("service_id") or "") for item in members if item.get("changed")})
    affected = sorted({service for item in items if item["classification"] != "unchanged" for service in item.get("affected_services") or []})
    unchanged_affected = sorted(service for service in affected if service not in set(changed_services))
    change_set = {
        "cluster_id": cluster_id,
        "source_kind": str((source or {}).get("kind") or ""),
        "since": str((source or {}).get("since") or ""),
        "until": str((source or {}).get("until") or ""),
        "repositories": [_change_row(item) for item in members],
    }
    return {
        "comparison_mode": mode,
        "change_set": change_set,
        "summary": summary,
        "items": items,
        "temporal_context": [_temporal_note(item) for item in temporal],
        "changed_services": changed_services,
        "affected_services": affected,
        "unchanged_but_affected_services": unchanged_affected,
        "relationships_examined": summary["examined"],
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
        "summary": analysis.get("summary") or {},
        "items": list(analysis.get("items") or []),
        "changed_services": analysis.get("changed_services") or [],
        "affected_services": analysis.get("affected_services") or [],
        "unchanged_but_affected_services": analysis.get("unchanged_but_affected_services") or [],
        "temporal_context": analysis.get("temporal_context") or [],
        "change_set": analysis.get("change_set") or {},
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
            f"{item.get('source_service') or ''} · {item.get('contract_id') or ''} · {item.get('target_service') or ''}",
            "",
            item.get("statement") or "",
            "",
        ])
        if item.get("required_action"):
            lines.extend([f"Action: {item.get('required_action')}", ""])
        evidence = ", ".join(item.get("evidence_ids") or [])
        if evidence:
            lines.extend([f"Evidence: {evidence}", ""])
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
        "- A historical co-change does not prove a dependency and cannot mark a relationship broken.",
        "- Repository Gate decisions were not rewritten.",
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
        "exact_boundary_snapshots": "Exact historical cluster graph comparison",
        "evidence_derived": "Evidence-derived change-set comparison",
        "partial": "Partial comparison",
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
        "assessment": record.get("assessment") or {},
        "summary": analysis.get("summary") or {},
        "changed_services": analysis.get("changed_services") or [],
        "affected_services": analysis.get("affected_services") or [],
        "unchanged_but_affected_services": analysis.get("unchanged_but_affected_services") or [],
        "change_set": analysis.get("change_set") or {},
        "interdependencies": analysis.get("items") or [],
        "temporal_context": analysis.get("temporal_context") or [],
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
    payload = render_json(record)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("cluster-review.json", json.dumps(payload, indent=2, sort_keys=True))
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
        classification = "modified"
        statement = f"{contract} changed. No break established."
        action = ""
    else:
        classification = "unchanged"
        statement = f"{consumer} still depends on {provider} {contract}."
        action = ""
    downstream = _downstream(relationships, consumer, provider)
    path = [
        {"service_id": provider, "role": "provider", "contract": contract},
        {"service_id": consumer, "role": "consumer", "contract": contract},
    ]
    if downstream:
        path.append({"service_id": downstream, "role": "downstream", "contract": ""})
    evidence = list(dict.fromkeys((removed or {}).get("evidence_ids", []) + (consumer_stopped or {}).get("evidence_ids", []) + (modified or {}).get("evidence_ids", [])))
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
        evidence=evidence,
        reviews=reviews,
        changed=changed,
        affected=affected,
        path=path,
        before_exists=True,
        after_exists=classification in {"unchanged", "modified", "coordinated_change"},
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
            evidence=added["evidence_ids"] + (provider_added or {}).get("evidence_ids", []),
            reviews=[item for item in {added["review_id"], (provider_added or {}).get("review_id", "")} if item],
            changed=[consumer, provider] if provider_added else [consumer],
            affected=[consumer, provider],
            path=[{"service_id": consumer, "role": "consumer", "contract": contract}, {"service_id": provider, "role": "provider", "contract": contract}],
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
    return {
        "source_service": kwargs["provider"],
        "target_service": kwargs["consumer"],
        "relationship": _relationship_name(kwargs["kind"]),
        "contract_id": kwargs["contract"],
        "before": {"exists": kwargs["before_exists"]},
        "after": {"exists": kwargs["after_exists"]},
        "classification": kwargs["classification"],
        "changed_services": kwargs["changed"],
        "affected_services": kwargs["affected"],
        "evidence_ids": kwargs["evidence"],
        "child_review_ids": kwargs["reviews"],
        "path": kwargs["path"],
        "statement": kwargs["statement"],
        "required_action": kwargs["action"],
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
    return {
        "service_id": member.get("service_id") or "",
        "changed": bool(member.get("changed") or member.get("change_detected")),
        "review_id": member.get("review_id") or "",
        "decision": member.get("decision") or "",
        "before_ref": member.get("before_ref") or "",
        "after_ref": member.get("after_ref") or "",
        "contract_changes": member.get("contract_changes") or [],
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
    names = {"http": "http_contract", "grpc": "grpc_contract", "event": "event_contract"}
    if kind in names:
        return names[kind]
    if kind.endswith("_contract"):
        return kind
    return "http_contract"


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
    explicit = str(item.get("contract") or item.get("contract_id") or item.get("contract_identity") or "").strip()
    method = str(item.get("method") or "").upper()
    path = str(item.get("path") or "").strip()
    if method and path:
        return f"{method} {path}"
    return explicit


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
    for item in relationships:
        source = str(item.get("from_service") or item.get("consumer") or "")
        target = str(item.get("to_service") or item.get("provider") or "")
        if source == consumer and target and target not in {consumer, provider}:
            return target
    return ""
