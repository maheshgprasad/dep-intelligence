"""Git-friendly Markdown for a stored intelligence report."""

from __future__ import annotations

from typing import Any


def render_markdown(document: dict[str, Any]) -> str:
    scope = document.get("scope")
    title = {"cluster": "Workspace", "workspace": "Workspace", "service_cluster": "Service Cluster", "repository": "Repository"}.get(str(scope), "Intelligence")
    lines = [
        f"# Nami Trace {title} Intelligence Report",
        "",
        document.get("executive_summary", {}).get("headline") or "",
        "",
        document.get("executive_summary", {}).get("summary") or "",
        "",
        f"Readiness: {document.get('executive_summary', {}).get('readiness_label') or ''}",
        "",
        "This report describes published Nami intelligence. It is not a Boundary Gate decision for a specific change.",
        "",
        "## What needs attention",
        "",
    ]
    attention = (document.get("recommendations") or [])[:5]
    if attention:
        for item in attention:
            lines.append(f"- {item.get('title')}. {item.get('reason')}")
    else:
        lines.append("No recommendations were recorded.")
    lines.extend(["", "## Intelligence status", ""])
    status = document.get("intelligence_status") or {}
    for key in ("structural", "history", "contracts", "health"):
        item = status.get(key) or {}
        lines.append(f"- {key.capitalize()}: {item.get('label') or item.get('status') or 'Unavailable'}")
    lines.extend(["", "## Observations", ""])
    for item in document.get("inferences") or []:
        lines.append(f"- {item.get('statement')}")
    if document.get("intelligence_gaps"):
        lines.extend(["", "## Intelligence gaps", ""])
        for item in document["intelligence_gaps"]:
            lines.append(f"- {item}")
    if scope in {"cluster", "workspace", "service_cluster"}:
        lines.extend(["", "## Services", "", "| Service | Graph | History | Health | Downstream |", "| --- | --- | --- | --- | --- |"])
        for service in document.get("services") or []:
            lines.append(
                f"| {service.get('service')} | {service.get('graph_label')} | {service.get('history_label')} | {service.get('health_label')} | {service.get('downstream')} |"
            )
        lines.extend(["", "## Contracts", "", "| Provider | Contract | Consumers |", "| --- | --- | --- |"])
        for row in (document.get("contracts") or {}).get("table") or []:
            lines.append(f"| {row.get('provider')} | {row.get('contract')} | {', '.join(row.get('consumers') or [])} |")
    else:
        lines.extend(["", "## Contracts provided", ""])
        for row in (document.get("contracts") or {}).get("provided") or []:
            consumers = ", ".join(row.get("consumers") or []) or "none recorded"
            lines.append(f"- {row.get('name')} ({row.get('kind')}). Consumers: {consumers}")
        lines.extend(["", "## Upstream dependencies", ""])
        for row in (document.get("relationships") or {}).get("upstream") or []:
            lines.append(f"- {row.get('to_service')} via {row.get('contract')}")
        lines.extend(["", "## Downstream dependents", ""])
        for row in (document.get("relationships") or {}).get("downstream") or []:
            lines.append(f"- {row.get('from_service')} via {row.get('contract')}")
    activity = document.get("review_activity") or {}
    counts = activity.get("counts") or {}
    lines.extend([
        "",
        "## Recent reviews",
        "",
        f"- Pass: {counts.get('pass', 0)}",
        f"- Warn: {counts.get('warn', 0)}",
        f"- Block: {counts.get('block', 0)}",
        f"- Incomplete: {counts.get('incomplete', 0)}",
        "",
        activity.get("note") or "Review counts describe Nami Gate outcomes and are not a software-quality score.",
        "",
    ])
    reviews = activity.get("attention_reviews")
    if reviews is None:
        reviews = [item for item in (activity.get("reviews") or []) if item.get("decision") != "pass"]
    total = int(activity.get("detail_included") or 0) + int(activity.get("detail_omitted") or 0) or len(reviews)
    shown = list(reviews)[:10]
    if total > len(shown):
        lines.append(f"Showing {len(shown)} of {total} attention-bearing reviews.")
        lines.append("")
    if not shown:
        lines.append("No attention-bearing reviews matched this report.")
    for item in shown:
        decision = str(item.get("decision") or "unknown").upper()
        lines.append(f"### {decision}. {item.get('headline') or item.get('service') or item.get('review_id')}")
        lines.append("")
        rule = item.get("primary_rule") or {}
        if rule:
            lines.append(f"Rule: {rule.get('title') or 'Stored rule'} {rule.get('rule_id') or ''}".strip())
            lines.append("")
        if item.get("service"):
            lines.append(f"Service: {item.get('service')}")
        targets = (item.get("targets") or {})
        files = list(dict.fromkeys(entry.get("file") for entry in (targets.get("items") or []) if entry.get("file")))[:5]
        if files or targets.get("total_findings"):
            lines.append(f"Review targets: {targets.get('unique_file_count') or len(files)} files · {targets.get('total_findings') or 0} supporting findings")
            for path in files:
                lines.append(f"- {path}")
            omitted = max(0, int(targets.get("unique_file_count") or 0) - len(files))
            if omitted:
                lines.append(f"- + {omitted} more")
        if item.get("why_it_matters"):
            lines.append(f"Why it matters: {item['why_it_matters']}")
        evidence = item.get("evidence") or []
        if evidence:
            entry = evidence[0]
            lines.append(f"Evidence: {entry.get('source') or 'Stored evidence'}. {entry.get('what') or entry.get('statement') or ''}")
        if item.get("next_action"):
            lines.append(f"Recommended action: {item['next_action']}")
        if item.get("validation"):
            lines.append(f"Decision logic: {item['validation']}")
        if item.get("review_id"):
            lines.append(f"Review ID: {item['review_id']}")
        lines.append("")
    lines.extend([
        "",
        "## How to interpret this report",
        "",
        "- Dependency reach is not a probability of failure.",
        "- Historical change association does not prove a structural dependency.",
        "- Workspace and repository readiness is not a Boundary Gate decision.",
        "- Missing or unscanned data is reported separately from clean results.",
        "",
        "## Limitations",
        "",
    ])
    for item in document.get("limitations") or []:
        lines.append(f"- {item}")
    identity = document.get("reproducibility") or {}
    lines.extend([
        "",
        "## Reproducibility",
        "",
        f"- Report: {document.get('report_id')}",
        f"- Graph version: {identity.get('graph_version')}",
        f"- Generated: {identity.get('generated_at')}",
        f"- Fingerprint: {identity.get('report_hash')}",
        "- Integrity fingerprint; not a cryptographic signature.",
        "",
    ])
    return "\n".join(lines)
