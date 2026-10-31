"""Git-friendly Markdown for a stored intelligence report."""

from __future__ import annotations

from typing import Any


def render_markdown(document: dict[str, Any]) -> str:
    scope = document.get("scope")
    lines = [
        f"# Nami Trace {'Workspace' if scope == 'cluster' else 'Repository'} Intelligence Report",
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
    lines.extend(["", "## Recommendations", ""])
    for item in document.get("recommendations") or []:
        lines.append(f"- {item.get('title')}. {item.get('reason')}")
    if scope == "cluster":
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
    reviews = activity.get("reviews") or []
    if not reviews:
        lines.append("No stored reviews matched this report.")
    for item in reviews:
        decision = str(item.get("decision") or "unknown").upper()
        lines.append(f"### {decision}. {item.get('headline') or item.get('service') or item.get('review_id')}")
        lines.append("")
        if item.get("what_happened"):
            lines.append(item["what_happened"])
            lines.append("")
        if item.get("why_it_matters"):
            lines.append(f"Why it matters: {item['why_it_matters']}")
            lines.append("")
        evidence = item.get("evidence") or []
        if evidence:
            lines.append("Evidence:")
            lines.append("")
            for entry in evidence:
                lines.append(f"- {entry.get('source') or 'Stored evidence'}: {entry.get('what') or ''}")
            lines.append("")
        else:
            lines.append("No evidence statements were stored with this review.")
            lines.append("")
        if item.get("validation"):
            lines.append(item["validation"])
            lines.append("")
        if item.get("next_action"):
            lines.append(f"Next: {item['next_action']}")
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
