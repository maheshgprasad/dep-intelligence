"""Workspace and service-cluster intelligence reports.

A workspace is every repository in the published snapshot. A service cluster is
one connected component of confirmed cross-service calls. Legacy scope
"cluster" is a workspace, not a service cluster.
"""

from __future__ import annotations

from typing import Any

from dep_intel.config import Settings
from dep_intel.graph_models import UNRESOLVED, Snapshot
from dep_intel.reporting.collect import (
    health_for,
    health_for_repository,
    history_for,
    label,
    relationships,
    reviews_for,
    topology_for,
)
from dep_intel.reporting.service_summary import service_summary
from dep_intel.service_clusters import detect_service_clusters


def build_workspace_document(
    settings: Settings,
    snapshot: Snapshot,
    *,
    members: set[str] | None = None,
    cluster: dict[str, Any] | None = None,
) -> dict[str, Any]:
    groups = detect_service_clusters(snapshot)
    scope = "service_cluster" if cluster else "workspace"
    selected = members
    relation = relationships(snapshot, members=selected)
    rows = relation["relationships"]
    health = health_for(settings)
    services = []
    history_ready = 0
    history_missing = []
    failed = []
    for service_id in sorted(snapshot.partitions):
        if selected is not None and service_id not in selected:
            continue
        part = snapshot.partitions[service_id]
        history = history_for(settings, part.repository, service_id=service_id)
        service_health = health_for_repository(settings, part.repository, service_id)
        if history["status"] == "available":
            history_ready += 1
        else:
            history_missing.append(service_id)
        if part.error:
            failed.append({"service": service_id, "error": part.error})
        services.append(service_summary(settings, snapshot, service_id, rows, history, service_health))
    stale = [item["service"] for item in services if item["graph_status"] != "current"]
    current = len(services) - len(stale)
    topology = topology_for(settings, selected)
    symbols = _symbol_count(snapshot, selected)
    contracts = _contracts(rows)
    concentration = _concentration(services, contracts)
    reviews = reviews_for(settings, members=selected, snapshot=snapshot)
    structural = "current" if services and not stale and not failed else "stale" if stale or failed else "unavailable"
    history_status = "available" if history_ready == len(services) and services else "unavailable" if history_ready == 0 else "partial"
    report_health = health if scope == "workspace" else _cluster_health(settings, snapshot, selected or set())
    health_status = _health_status(report_health)
    readiness = "limited" if stale or failed or not services else "partial" if history_status != "available" or health_status not in {"available", "complete"} else "complete"
    provider_count = int(topology.get("unique_unresolved_reference_count") or 0)
    gaps = _gaps(stale, topology, history_missing, report_health, failed)
    recommendations = _recommendations(stale, topology, contracts, report_health, history_missing)
    limitations = _limitations(stale, topology, history_missing, report_health, failed, relation, scope)
    narrative = _narrative(services, relation, contracts, topology, stale, history_ready, scope, cluster)
    shown = relation["items"]
    identity: dict[str, Any] = {
        "graph_version": snapshot.version,
        "generated_at_graph": snapshot.generated_at,
        "revision_vector": {
            key: value for key, value in snapshot.revision_vector.items() if selected is None or key in selected
        },
    }
    if cluster:
        identity.update({
            "cluster_id": cluster["cluster_id"],
            "cluster_label": cluster["label"],
            "services": cluster["services"],
            "graph_version": snapshot.version,
        })
    cluster_items = groups["clusters"] if scope == "workspace" else ([cluster] if cluster else [])
    standalone = groups["standalone"] if scope == "workspace" else []
    document = {
        "scope": scope,
        "identity": identity,
        "executive_summary": {
            "headline": cluster["label"] if cluster else "Workspace intelligence",
            "summary": narrative,
            "readiness": readiness,
            "readiness_label": label(readiness),
        },
        "intelligence_status": {
            "structural": {"status": structural, "label": f"{current} / {len(services)} current" if services else label(structural), "current": current, "total": len(services)},
            "history": {"status": history_status, "label": f"{history_ready} / {len(services)} available" if services else label(history_status), "ready": history_ready, "total": len(services)},
            "contracts": {"status": "current" if services else "unavailable", "label": label("current" if services else "unavailable")},
            "health": {"status": health_status, "label": label(health_status)},
        },
        "workspace": {
            "repository_count": len({item["repository"] for item in services}),
            "service_count": len(services),
            "graph_current": current,
            "graph_stale": len(stale),
            "history_ready": history_ready,
            "contract_count": contracts["count"],
            "relationship_count": relation["total"],
            "unresolved_count": provider_count if topology.get("status") != "not_scanned" else 0,
            "failed": failed,
        },
        "service_clusters": {"count": len(cluster_items), "items": [_public_cluster(item) for item in cluster_items if item]},
        "standalone": standalone,
        "services": services,
        "relationships": {
            "items": shown,
            "total": relation["total"],
            "included": relation["included"],
            "truncated": relation["truncated"],
            "provenance": relation["provenance"],
            "resolution": relation["resolution"],
            "note": f"Showing {len(shown)} of {relation['total']} confirmed relationships." if relation["truncated"] else "",
        },
        "contracts": contracts,
        "concentration": concentration,
        "history": {
            "status": history_status,
            "label": label(history_status),
            "ready": history_ready,
            "missing": history_missing,
            "note": "Historical change association is not a structural dependency.",
        },
        "health": report_health,
        "provider_discovery": topology,
        "code_graph_diagnostics": {
            "unresolved_symbol_count": symbols,
            "note": "Code-graph unresolved symbols are not unresolved service providers.",
        },
        "review_activity": reviews,
        "inferences": _inferences(services, contracts, stale, topology, history_missing, report_health),
        "recommendations": recommendations,
        "intelligence_gaps": gaps,
        "limitations": limitations,
        "unresolved": topology.get("items") or [],
    }
    if scope == "service_cluster":
        document["health_note"] = "Health shown here is limited to stored records that identify a member repository."
    return document


def _cluster_health(settings: Settings, snapshot: Snapshot, members: set[str]) -> dict[str, Any]:
    rows = [
        health_for_repository(settings, snapshot.partitions[service_id].repository, service_id)
        for service_id in sorted(members)
        if service_id in snapshot.partitions
    ]
    if not rows or all(item["packages"]["status"] == "workspace_only" for item in rows):
        return rows[0] if rows else health_for(settings)
    combined = {}
    for name in ("packages", "updates", "security", "coverage"):
        matched = [item[name] for item in rows if item[name]["status"] == "available"]
        if matched:
            combined[name] = {
                "status": "available",
                "label": label("available"),
                "detail": "Counts include only member repositories with a stored breakdown.",
                "finding_count": sum(int(item.get("finding_count") or 0) for item in matched),
            }
        else:
            combined[name] = rows[0][name]
    return combined


def _public_cluster(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "cluster_id": item.get("cluster_id"),
        "label": item.get("label"),
        "service_count": item.get("service_count"),
        "relationship_count": item.get("relationship_count"),
        "services": item.get("services") or [],
    }


def _symbol_count(snapshot: Snapshot, members: set[str] | None) -> int:
    return sum(
        1
        for node in snapshot.nodes.values()
        if node.category == UNRESOLVED and (members is None or node.service_id in members)
    )


def _contracts(rows: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[tuple[str, str, str], set[str]] = {}
    for row in rows:
        key = (row.get("to_service") or "", row.get("contract") or "", row.get("kind") or "")
        grouped.setdefault(key, set()).add(row.get("from_service") or "")
    table = [
        {"provider": provider, "contract": contract, "kind": kind, "consumers": sorted(consumers)}
        for (provider, contract, kind), consumers in grouped.items()
    ]
    table.sort(key=lambda item: (-len(item["consumers"]), item["provider"], item["contract"]))
    return {"table": table, "count": len(table)}


def _concentration(services: list[dict[str, Any]], contracts: dict[str, Any]) -> dict[str, Any]:
    downstream = max(services, key=lambda item: item["downstream"], default=None)
    upstream = max(services, key=lambda item: item["upstream"], default=None)
    widest = max(contracts["table"], key=lambda item: len(item["consumers"]), default=None)
    return {
        "widest_downstream": {"service": downstream["service"], "count": downstream["downstream"]} if downstream and downstream["downstream"] else None,
        "most_upstream": {"service": upstream["service"], "count": upstream["upstream"]} if upstream and upstream["upstream"] else None,
        "widest_contract": {"provider": widest["provider"], "contract": widest["contract"], "consumers": len(widest["consumers"])} if widest and widest["consumers"] else None,
        "note": "Dependency concentration describes confirmed reach. It is not a risk score.",
    }


def _health_status(health: dict[str, Any]) -> str:
    states = [health["packages"]["status"], health["security"]["status"], health["coverage"]["status"]]
    if any(item == "workspace_only" for item in states):
        return "partial"
    if all(item == "available" for item in states):
        return "available"
    if any(item == "available" for item in states):
        return "partial"
    if all(item == "not_scanned" for item in states):
        return "not_scanned"
    return "unavailable"


def _gaps(stale, topology, history_missing, health, failed) -> list[str]:
    gaps = []
    if stale:
        gaps.append(f"{len(stale)} services have out-of-date structural intelligence.")
    if topology.get("status") == "not_scanned":
        gaps.append("Topology provider discovery: Not scanned.")
    elif topology.get("unique_unresolved_reference_count"):
        gaps.append(f"{topology['unique_unresolved_reference_count']} unique provider references remain unresolved.")
    if history_missing:
        gaps.append(f"{len(history_missing)} repositories have no usable history.")
    if health.get("coverage", {}).get("status") == "not_scanned":
        gaps.append("Coverage has not been generated.")
    if health.get("security", {}).get("status") == "not_scanned":
        gaps.append("A security scan has not been stored.")
    for item in failed:
        gaps.append(f"{item['service']} failed graph extraction.")
    return gaps


def _inferences(services, contracts, stale, topology, history_missing, health) -> list[dict[str, Any]]:
    items = []
    if stale:
        items.append({"type": "stale_intelligence_present", "statement": f"{len(stale)} services have out-of-date structural intelligence.", "evidence": ["graph"]})
    if topology.get("status") == "not_scanned":
        items.append({"type": "topology_not_scanned", "statement": "Topology provider discovery: Not scanned.", "evidence": ["topology"]})
    elif topology.get("unique_unresolved_reference_count"):
        count = topology["unique_unresolved_reference_count"]
        observations = topology.get("unresolved_observation_count")
        items.append({"type": "unresolved_topology_present", "statement": f"{count} unique provider references are unresolved ({observations} observations).", "evidence": ["topology"]})
    if history_missing:
        items.append({"type": "history_coverage_incomplete", "statement": f"{len(history_missing)} repositories have no usable history.", "evidence": ["history"]})
    if health.get("coverage", {}).get("status") == "not_scanned":
        items.append({"type": "coverage_incomplete", "statement": "Coverage data has not been generated.", "evidence": ["health"]})
    widest = max(services, key=lambda item: item["downstream"], default=None)
    if widest and widest["downstream"]:
        items.append({"type": "dependency_concentration", "statement": f"{widest['service']} has the widest confirmed downstream reach ({widest['downstream']} consumers).", "evidence": ["relationships"]})
    multi = [item for item in contracts["table"] if len(item["consumers"]) > 1]
    if multi:
        items.append({"type": "multi_consumer_contract", "statement": f"{multi[0]['contract']} is consumed by {len(multi[0]['consumers'])} services.", "evidence": ["contracts"]})
    return items[:8]


def _recommendations(stale, topology, contracts, health, history_missing) -> list[dict[str, Any]]:
    items = []
    for service in stale[:3]:
        items.append({"title": f"Refresh {service} intelligence", "reason": "The structural graph is out of date.", "service": service})
    if topology.get("status") != "not_scanned" and topology.get("unique_unresolved_reference_count"):
        example = (topology.get("items") or [{}])[0]
        host = str(example.get("host") or example.get("name") or "").strip()
        if host and not host.startswith("$"):
            items.append({"title": f"Resolve {host}", "reason": "The provider reference is not mapped to an analyzed service.", "service": example.get("service") or example.get("from")})
        else:
            items.append({"title": "Resolve unresolved provider references", "reason": f"{topology['unique_unresolved_reference_count']} unique provider references are not mapped to an analyzed service.", "service": None})
    widest = max(contracts["table"], key=lambda item: len(item["consumers"]), default=None)
    if widest and widest["consumers"]:
        items.append({"title": f"Coordinate {widest['contract']} changes", "reason": f"{len(widest['consumers'])} known consumers depend on {widest['provider']}.", "service": widest["provider"]})
    if health.get("coverage", {}).get("status") == "not_scanned" and history_missing:
        items.append({"title": f"Generate coverage for {history_missing[0]}", "reason": "Coverage has not been scanned, so verification context is incomplete.", "service": history_missing[0]})
    elif health.get("coverage", {}).get("status") == "not_scanned":
        items.append({"title": "Generate coverage", "reason": "Coverage has not been scanned.", "service": None})
    return items[:6]


def _limitations(stale, topology, history_missing, health, failed, relation, scope: str) -> list[str]:
    items = [
        "This report describes published intelligence. It is not a Boundary Gate decision.",
        "Historical change association is not a structural dependency.",
        "A service cluster is a connected component of confirmed cross-service relationships. It is not a Kubernetes cluster.",
        "Automatic protobuf, gRPC, and broker discovery are not claimed.",
        "Nami Rank is not executed for this report.",
    ]
    if scope == "service_cluster":
        items.append("Membership is the confirmed component stored with this report. A later review does not add services.")
    for service in stale:
        items.append(f"{service} graph is out of date.")
    for service in history_missing:
        items.append(f"Historical data was unavailable for {service}.")
    if topology.get("status") == "not_scanned":
        items.append("Topology provider discovery: Not scanned.")
    elif topology.get("unique_unresolved_reference_count"):
        items.append(f"{topology['unique_unresolved_reference_count']} unique provider references could not be resolved.")
    if health.get("coverage", {}).get("status") == "not_scanned":
        items.append("Coverage has not been generated.")
    for item in failed:
        items.append(f"{item['service']} failed graph extraction: {item['error']}")
    if relation.get("truncated"):
        items.append(f"Showing {relation['included']} of {relation['total']} confirmed relationships. Aggregate counts use the full set.")
    return items


def _narrative(services, relation, contracts, topology, stale, history_ready, scope: str, cluster: dict[str, Any] | None) -> str:
    if not services:
        return "No published service intelligence is available."
    if cluster:
        lead = f"{cluster['label']} contains {len(services)} services connected by {relation['total']} confirmed cross-service calls."
    else:
        lead = f"The workspace contains {len(services)} analyzed services"
        if relation["total"]:
            lead += f" and {relation['total']} confirmed cross-service calls"
        lead += "."
    bits = [lead]
    if stale:
        bits.append(f"{len(stale)} services contain out-of-date structural intelligence.")
    if topology.get("status") == "not_scanned":
        bits.append("Topology provider discovery: Not scanned.")
    elif topology.get("unique_unresolved_reference_count"):
        bits.append(f"{topology['unique_unresolved_reference_count']} unique provider references remain unresolved.")
    bits.append(f"{history_ready} repositories have usable history.")
    if contracts["count"]:
        bits.append(f"{contracts['count']} cross-service contracts are recorded.")
    return " ".join(bits)
