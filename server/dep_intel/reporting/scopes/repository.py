"""One-service intelligence report. Observational only; not a Gate decision."""

from __future__ import annotations

from typing import Any

from dep_intel.config import Settings
from dep_intel.graph_models import Snapshot
from dep_intel.reporting.collect import (
    architecture_of,
    code_symbol_count,
    contracts_of,
    directions,
    health_for_repository,
    history_for,
    label,
    relationships,
    reviews_for,
    topology_for,
)
from dep_intel.reporting.service_summary import service_summary


def build_repository_document(settings: Settings, snapshot: Snapshot, service_id: str) -> dict[str, Any]:
    if service_id not in snapshot.partitions:
        raise KeyError(service_id)
    part = snapshot.partitions[service_id]
    relation_payload = relationships(snapshot, service_id)
    rows = relation_payload["relationships"]
    history = history_for(settings, part.repository, service_id=service_id)
    health = health_for_repository(settings, part.repository, service_id)
    reviews = reviews_for(settings, service=service_id, repository=part.repository, snapshot=snapshot)
    architecture = architecture_of(settings, snapshot, service_id)
    contracts = contracts_of(snapshot, service_id, rows)
    linked = directions(service_id, rows)
    topology = topology_for(settings, {service_id})
    summary = service_summary(settings, snapshot, service_id, rows, history, health)
    structural = "stale" if part.quality != "ok" or part.error else "current"
    health_status = _health_status(health)
    readiness = _readiness(structural, history["status"], health_status)
    inferences = _inferences(summary, history, health, topology, contracts, linked)
    recommendations = _recommendations(part, history, health, topology, contracts)
    limitations = _limitations(part, history, health, topology, relation_payload)
    narrative = _narrative(summary, structural, history, health_status, topology)
    return {
        "scope": "repository",
        "identity": {
            "service": service_id,
            "repository": part.repository,
            "repository_slug": history.get("slug") or "",
            "repository_sha": part.workspace_head,
            "graph_version": snapshot.version,
            "index_revision": part.index_revision,
            "languages": [],
            "frameworks": [],
            "graph_quality": part.quality,
            "graph_freshness": label(structural),
        },
        "executive_summary": {
            "headline": f"{service_id} intelligence",
            "summary": narrative,
            "readiness": readiness,
            "readiness_label": label(readiness),
        },
        "intelligence_status": {
            "structural": {"status": structural, "label": label(structural)},
            "history": {"status": history["status"], "label": history["label"]},
            "contracts": {"status": "current" if part.quality == "ok" else structural, "label": label("current" if part.quality == "ok" else structural)},
            "health": {"status": health_status, "label": label(health_status)},
        },
        "service": summary,
        "architecture": architecture,
        "contracts": contracts,
        "relationships": {
            "upstream": linked["upstream"][:100],
            "downstream": linked["downstream"][:100],
            "total": relation_payload["total"],
            "truncated": relation_payload["truncated"],
            "included": relation_payload["included"],
            "provenance": relation_payload["provenance"],
            "resolution": relation_payload["resolution"],
        },
        "history": history,
        "health": health,
        "review_activity": reviews,
        "inferences": inferences,
        "recommendations": recommendations,
        "provider_discovery": topology,
        "code_graph_diagnostics": {
            "unresolved_symbol_count": code_symbol_count(snapshot, service_id),
            "note": "Code-graph unresolved symbols are not unresolved service providers.",
        },
        "intelligence_gaps": [item["statement"] for item in inferences if item["type"] in {"stale_graph", "no_history", "missing_coverage", "unresolved_provider", "topology_not_scanned"}],
        "limitations": limitations,
    }


def _health_status(health: dict[str, Any]) -> str:
    states = [health["packages"]["status"], health["security"]["status"], health["coverage"]["status"]]
    if all(item == "not_scanned" for item in states):
        return "not_scanned"
    if all(item == "available" for item in states):
        return "available"
    if any(item == "available" for item in states):
        return "partial"
    return "unavailable"


def _readiness(structural: str, history: str, health_status: str) -> str:
    if structural != "current":
        return "limited"
    if history != "available" or health_status in {"not_scanned", "unavailable", "partial"}:
        return "partial"
    return "complete"


def _narrative(summary: dict[str, Any], structural: str, history: dict[str, Any], health_status: str, topology: dict[str, Any]) -> str:
    parts = [
        f"{summary['symbols']} code symbols.",
        f"{summary['internal_relationships']} internal dependency relationships.",
        f"{summary['provides']} exposed contracts.",
        f"{summary['downstream']} downstream consumers.",
        f"{summary['upstream']} upstream service dependencies.",
    ]
    if topology.get("status") == "not_scanned":
        parts.append("Topology provider discovery: Not scanned.")
    elif topology.get("unique_unresolved_reference_count"):
        parts.append(f"{topology['unique_unresolved_reference_count']} unique provider references remain unresolved.")
    else:
        parts.append("No unresolved provider references were recorded for this service.")
    parts.append("Historical evidence is available." if history["status"] == "available" else "Historical evidence is not available.")
    parts.append(f"Repository health data is {label(health_status).lower()}.")
    parts.append(f"Structural intelligence is {label(structural).lower()}.")
    return " ".join(parts)


def _inferences(summary, history, health, topology, contracts, linked) -> list[dict[str, Any]]:
    items = []
    if summary["downstream"]:
        items.append({"type": "cross_service_dependency", "statement": f"This service has {summary['downstream']} known downstream consumers.", "evidence": ["relationships"]})
    multi = [item for item in contracts["provided"] if len(item.get("consumers") or []) > 1]
    if multi:
        items.append({"type": "multi_consumer_contract", "statement": f"{multi[0]['name']} is consumed by {len(multi[0]['consumers'])} services.", "evidence": ["contracts"]})
    if history["status"] != "available":
        items.append({"type": "no_history", "statement": "This repository has structural intelligence and no usable history.", "evidence": ["history"]})
    if summary["graph_status"] != "current":
        items.append({"type": "stale_graph", "statement": "The structural graph for this service is out of date.", "evidence": ["graph"]})
    if health["coverage"]["status"] == "not_scanned":
        items.append({"type": "missing_coverage", "statement": "Coverage data has not been generated for this repository.", "evidence": ["health"]})
    if health["packages"]["status"] == "workspace_only":
        items.append({"type": "workspace_health_only", "statement": "Workspace-wide health data is available, but a repository-specific breakdown is not recorded.", "evidence": ["health"]})
    if topology.get("status") == "not_scanned":
        items.append({"type": "topology_not_scanned", "statement": "Topology provider discovery: Not scanned.", "evidence": ["topology"]})
    elif topology.get("unique_unresolved_reference_count"):
        items.append({"type": "unresolved_provider", "statement": f"{topology['unique_unresolved_reference_count']} unique provider references remain unresolved.", "evidence": ["topology"]})
    if summary["downstream"] >= 2:
        items.append({"type": "high_dependency_concentration", "statement": "This service has a wide confirmed downstream reach.", "evidence": ["relationships"]})
    if linked["upstream"] or linked["downstream"]:
        items.append({"type": "cross_service_dependency", "statement": "Cross-service relationships are recorded for this service.", "evidence": ["relationships"]})
    deduped = []
    seen = set()
    for item in items:
        key = (item["type"], item["statement"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped[:8]


def _recommendations(part, history, health, topology, contracts) -> list[dict[str, Any]]:
    items = []
    if part.quality != "ok" or part.error:
        items.append({"title": f"Refresh {part.service_id} intelligence", "reason": "The structural graph is out of date or incomplete.", "service": part.service_id})
    if topology.get("status") != "not_scanned" and topology.get("unique_unresolved_reference_count"):
        items.append({"title": "Resolve unresolved provider references", "reason": f"{topology['unique_unresolved_reference_count']} unique provider references are not mapped to an analyzed service.", "service": part.service_id})
    if health["coverage"]["status"] == "not_scanned":
        items.append({"title": "Generate coverage", "reason": "Coverage has not been scanned, so test-readiness is unknown.", "service": part.service_id})
    for contract in contracts["provided"]:
        if contract["consumers"]:
            names = ", ".join(contract["consumers"])
            items.append({"title": f"Coordinate changes to {contract['name']}", "reason": f"Known consumers: {names}.", "service": part.service_id})
            break
    if history["status"] != "available":
        items.append({"title": "Collect repository history before relying on change association", "reason": "No usable commit history is stored for this repository.", "service": part.service_id})
    return items[:6]


def _limitations(part, history, health, topology, relation_payload) -> list[str]:
    items = [
        "This report describes published intelligence. It is not a Boundary Gate decision.",
        "Historical change association is not a structural dependency.",
        "Automatic protobuf, gRPC, and broker discovery are not claimed.",
    ]
    if part.quality != "ok" or part.error:
        items.append(f"{part.service_id} structural intelligence is out of date or failed.")
    if history["status"] != "available":
        items.append("Historical data was unavailable for this repository.")
    if health["coverage"]["status"] == "not_scanned":
        items.append("Coverage has not been generated.")
    if health["security"]["status"] == "not_scanned":
        items.append("A security scan has not been stored.")
    if topology.get("status") == "not_scanned":
        items.append("Topology provider discovery: Not scanned.")
    elif topology.get("unique_unresolved_reference_count"):
        items.append(f"{topology['unique_unresolved_reference_count']} unique provider references could not be resolved.")
    if health["packages"]["status"] == "workspace_only":
        items.append("Workspace-wide health data is available, but a repository-specific breakdown is not recorded.")
    if relation_payload.get("truncated"):
        items.append("Detailed relationship rows were truncated. Use the evidence package for the included bounded set.")
    if part.error:
        items.append(part.error)
    return items
