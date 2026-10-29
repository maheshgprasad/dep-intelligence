"""Git-friendly Markdown for a stored intelligence report."""

from __future__ import annotations

from typing import Any


def render_markdown(document: dict[str, Any]) -> str:
    scope = document.get("scope")
    lines = [
        f"# Nami Trace {'cluster' if scope == 'cluster' else 'repository'} intelligence report",
        "",
        document.get("executive_summary", {}).get("headline") or "",
        "",
        document.get("executive_summary", {}).get("summary") or "",
        "",
        f"Readiness: {document.get('executive_summary', {}).get('readiness_label') or ''}",
        "",
        "## Intelligence status",
        "",
    ]
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
    lines.extend(["", "## Limitations", ""])
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
