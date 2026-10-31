"""Git-friendly Markdown for a stored intelligence report."""

from __future__ import annotations

from typing import Any

from dep_intel.reporting.review_summary import human_validation_lines

INTERPRETATION = "Workspace, Service Cluster, and Repository readiness describe intelligence completeness. They are not Boundary Gate decisions."
_COUNT_NOTE = "Counts include all stored reviews that matched this report scope at generation time."


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
    if scope in {"cluster", "workspace", "service_cluster"}:
        lines.extend(_scope_sections(document))
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
    lines.extend(_review_section(document.get("review_activity") or {}))
    lines.extend(["", "## Key observations", ""])
    observations = document.get("inferences") or []
    if observations:
        for item in observations:
            lines.append(f"- {item.get('statement')}")
    else:
        lines.append("No additional observations were recorded.")
    if document.get("intelligence_gaps"):
        lines.extend(["", "## Intelligence gaps", ""])
        for item in document["intelligence_gaps"]:
            lines.append(f"- {item}")
    lines.extend([
        "",
        "## How to interpret this report",
        "",
        "- Dependency reach is not a probability of failure.",
        "- Historical change association does not prove a structural dependency.",
        f"- {INTERPRETATION}",
        "- Missing or unscanned data is reported separately from clean results.",
        "",
        "## Limitations",
        "",
    ])
    for item in document.get("limitations") or []:
        lines.append(f"- {item}")
    identity = document.get("identity") or {}
    reproducibility = document.get("reproducibility") or {}
    lines.extend([
        "",
        "## Reproducibility",
        "",
        f"- Report: {document.get('report_id')}",
        f"- Graph version: {reproducibility.get('graph_version')}",
        f"- Generated: {reproducibility.get('generated_at')}",
    ])
    if identity.get("cluster_id"):
        lines.append(f"- Service cluster ID: {identity.get('cluster_id')}")
    if identity.get("service"):
        lines.append(f"- Service: {identity.get('service')}")
    lines.extend([
        f"- Fingerprint: {reproducibility.get('report_hash')}",
        "- Integrity fingerprint; not a cryptographic signature.",
        "",
    ])
    return "\n".join(lines)


def _scope_sections(document: dict[str, Any]) -> list[str]:
    scope = document.get("scope")
    workspace = document.get("workspace") or {}
    identity = document.get("identity") or {}
    lines: list[str] = []
    if scope == "service_cluster":
        lines.extend([
            "",
            "## Service cluster",
            "",
            f"Cluster: {identity.get('cluster_label') or 'Connected service group'}",
            f"Cluster ID: {identity.get('cluster_id') or ''}",
            f"Member services: {workspace.get('service_count')}",
            f"Confirmed internal calls: {workspace.get('relationship_count')}",
            "",
            "This report contains one connected component of confirmed cross-service relationships.",
            "",
            "Repositories outside this component are not included.",
            "",
            "Use the Workspace report for the complete repository allowlist.",
            "",
            "## Cluster membership",
            "",
        ])
        members = [str(item) for item in (identity.get("services") or []) if item]
        shown = members[:30]
        for name in shown:
            lines.append(f"- {name}")
        extra = len(members) - len(shown)
        if extra:
            lines.append(f"- + {extra} additional member services")
        if not shown:
            lines.append("Membership was not stored on this report.")
    else:
        lines.extend([
            "",
            "## Workspace overview",
            "",
            f"- Services: {workspace.get('service_count')}",
            f"- Confirmed cross-service calls: {workspace.get('relationship_count')}",
            f"- Contracts: {workspace.get('contract_count')}",
            "",
        ])
        groups = ((document.get("service_clusters") or {}).get("items") or [])
        if groups:
            lines.extend(["## Detected service clusters", ""])
            for item in groups:
                lines.append(f"- {item.get('label') or 'Connected service group'}. {item.get('service_count')} services. {item.get('relationship_count')} confirmed calls.")
            lines.append("")
        standalone = document.get("standalone") or []
        if standalone:
            lines.extend([
                "## Standalone repositories",
                "",
                "A standalone repository or service has no confirmed cross-service relationship in this stored snapshot.",
                "",
            ])
            for item in standalone:
                lines.append(f"- {item.get('service_id') or item.get('service') or ''}")
            lines.append("")
    lines.extend(["## Service catalogue", "", "| Service | Graph | History | Health | Downstream |", "| --- | --- | --- | --- | --- |"])
    for service in document.get("services") or []:
        lines.append(
            f"| {service.get('service')} | {service.get('graph_label')} | {service.get('history_label')} | {service.get('health_label')} | {service.get('downstream')} |"
        )
    relation = document.get("relationships") or {}
    heading = "Internal cross-service relationships" if scope == "service_cluster" else "Cross-service relationships"
    lines.extend(["", f"## {heading}", ""])
    for row in (relation.get("items") or [])[:100]:
        lines.append(f"- {row.get('from_service')} → {row.get('to_service')} via {row.get('contract')}")
    provenance = relation.get("provenance") or {}
    if provenance:
        lines.extend([
            "",
            "## Relationship provenance",
            "",
        ])
        for key, count in provenance.items():
            lines.append(f"- {str(key).capitalize()}: {count}")
        lines.extend(["", "These counts describe stored relationship evidence. They do not imply that every relationship was automatically discovered."])
    lines.extend(["", "## Contracts", "", "| Provider | Contract | Consumers |", "| --- | --- | --- |"])
    for row in (document.get("contracts") or {}).get("table") or []:
        lines.append(f"| {row.get('provider')} | {row.get('contract')} | {', '.join(row.get('consumers') or [])} |")
    discovery = document.get("provider_discovery")
    if isinstance(discovery, dict) and discovery:
        lines.extend(["", "## Provider discovery", ""])
        if discovery.get("status") == "not_scanned":
            lines.append("Status: Not scanned")
        else:
            lines.append(f"Status: {discovery.get('label') or 'Completed'}")
            lines.append(f"Unique unresolved references: {discovery.get('unique_unresolved_reference_count')}")
            lines.append(f"Unresolved observations: {discovery.get('unresolved_observation_count')}")
        lines.append("")
        lines.append("Provider discovery is separate from the confirmed service graph.")
    return lines


def _review_section(activity: dict[str, Any]) -> list[str]:
    counts = activity.get("counts") or {}
    note = str(activity.get("note") or "Review counts describe Nami Gate outcomes and are not a software-quality score.")
    if _COUNT_NOTE not in note:
        note = f"{_COUNT_NOTE} {note}"
    lines = [
        "",
        "## Review activity",
        "",
        f"- Pass: {counts.get('pass', 0)}",
        f"- Warn: {counts.get('warn', 0)}",
        f"- Block: {counts.get('block', 0)}",
        f"- Incomplete: {counts.get('incomplete', 0)}",
        "",
        note,
        "",
        "## Reviews requiring attention",
        "",
    ]
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
        targets = item.get("targets") or {}
        files = list(dict.fromkeys(entry.get("file") for entry in (targets.get("items") or []) if entry.get("file")))[:5]
        if files or targets.get("total_findings"):
            lines.append(f"Review targets: {targets.get('unique_file_count') or len(files)} files · {targets.get('total_findings') or 0} supporting findings")
            for path in files:
                lines.append(f"- {path}")
            omitted = max(0, int(targets.get("unique_file_count") or 0) - len(files))
            if omitted:
                lines.append(f"- + {omitted} more")
        why = _why_text(item)
        if why:
            lines.append(f"Why it matters: {why}")
        evidence = item.get("evidence") or []
        if evidence:
            entry = evidence[0]
            lines.append(f"Evidence: {entry.get('source') or 'Stored evidence'}. {entry.get('what') or entry.get('statement') or ''}")
        ranking = item.get("ranking_context") or {}
        if ranking.get("statement"):
            lines.append(f"Ranking context: {ranking.get('source') or 'Nami Rank'}. {ranking.get('statement')}")
        outside = [str(name) for name in (item.get("cross_cluster_targets") or []) if name]
        if outside:
            lines.append("External review targets:")
            for name in outside:
                lines.append(f"- {name}")
            lines.append("These services are outside the selected Service Cluster.")
        if item.get("next_action"):
            lines.append(f"Recommended action: {item['next_action']}")
        lines.extend(human_validation_lines(item))
        if item.get("review_id"):
            lines.append(f"Review ID: {item['review_id']}")
        lines.append("")
    return lines


def _why_text(item: dict[str, Any]) -> str:
    why = str(item.get("why_it_matters") or "")
    rule = str((item.get("primary_rule") or {}).get("rule_id") or "")
    if "TEMPORAL" not in rule:
        return why
    if "not a blocker" not in why.lower():
        why = f"Historical association is not a blocker. {why}".strip()
    if "does not confirm a current structural dependency" not in why.lower():
        why = f"{why} This evidence alone does not confirm a current structural dependency.".strip()
    return why
