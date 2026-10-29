"""Workspace intelligence report. Observational only; not a Gate decision."""

from __future__ import annotations

import re
from typing import Any

from dep_intel.config import Settings
from dep_intel.graph_models import Snapshot
from dep_intel.reporting.collect import health_for, history_for, label, relationships, reviews_for, unresolved_of
from dep_intel.reporting.service_summary import service_summary

_PLACEHOLDER = re.compile(r"^\$\d+$")


def build_cluster_document(settings: Settings, snapshot: Snapshot) -> dict[str, Any]:
    relation_payload = relationships(snapshot)
    rows = relation_payload["relationships"]
    health = health_for(settings)
    services = []
    history_ready = 0
    history_missing = []
    failed = []
    for service_id in sorted(snapshot.partitions):
        part = snapshot.partitions[service_id]
        history = history_for(settings, part.repository)
        if history["status"] == "available":
            history_ready += 1
        else:
            history_missing.append(service_id)
        if part.error:
            failed.append({"service": service_id, "error": part.error})
        services.append(service_summary(settings, snapshot, service_id, rows, history, health))
    stale = [item["service"] for item in services if item["graph_status"] != "current"]
    current = len(services) - len(stale)
    unresolved = unresolved_of(snapshot)
    contracts = _contracts(rows)
    concentration = _concentration(services, contracts)
    reviews = reviews_for(settings, limit=20)
    structural = "current" if services and not stale and not failed else "stale" if stale or failed else "unavailable"
    history_status = "available" if history_ready == len(services) and services else "unavailable" if history_ready == 0 else "partial"
    health_status = _health_status(health)
    readiness = "limited" if stale or failed or not services else "partial" if history_status != "available" or health_status != "available" else "complete"
    gaps = _gaps(stale, unresolved, history_missing, health, failed)
    inferences = _inferences(services, contracts, stale, unresolved, history_missing, health)
    recommendations = _recommendations(stale, unresolved, contracts, health, history_missing)
    limitations = _limitations(stale, unresolved, history_missing, health, failed, relation_payload)
    narrative = _narrative(services, rows, contracts, unresolved, stale, history_ready)
    return {
        "scope": "cluster",
        "identity": {
            "graph_version": snapshot.version,
            "generated_at_graph": snapshot.generated_at,
            "revision_vector": snapshot.revision_vector,
        },
        "executive_summary": {
            "headline": "Cluster intelligence",
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
            "contract_count": snapshot.summary()["contract_count"],
            "relationship_count": relation_payload["total"],
            "unresolved_count": len(unresolved),
            "failed": failed,
        },
        "services": services,
        "relationships": {
            "items": rows,
            "total": relation_payload["total"],
            "included": relation_payload["included"],
            "truncated": relation_payload["truncated"],
            "provenance": _provenance_counts(rows),
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
        "health": health,
        "review_activity": reviews,
        "intelligence_gaps": gaps,
        "inferences": inferences,
        "recommendations": recommendations,
        "limitations": limitations,
        "unresolved": unresolved,
    }


def _contracts(rows: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[tuple[str, str, str], set[str]] = {}
    for row in rows:
        key = (row.get("to_service") or "", row.get("contract") or "", row.get("kind") or "")
        grouped.setdefault(key, set()).add(row.get("from_service") or "")
    table = [
        {"provider": provider, "contract": contract, "kind": kind, "consumers": sorted(consumers)}
        for (provider, contract, kind), consumers in grouped.items()
    ]
    table.sort(key=lambda item: (item["provider"], item["contract"]))
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


def _provenance_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        key = str(row.get("provenance") or "unknown")
        counts[key] = counts.get(key, 0) + 1
    return counts


def _health_status(health: dict[str, Any]) -> str:
    states = [health["packages"]["status"], health["security"]["status"], health["coverage"]["status"]]
    if all(item == "available" for item in states):
        return "available"
    if any(item == "available" for item in states):
        return "partial"
    if all(item == "not_scanned" for item in states):
        return "not_scanned"
    return "unavailable"


def _gaps(stale, unresolved, history_missing, health, failed) -> list[str]:
    gaps = []
    if stale:
        gaps.append(f"{len(stale)} services have out-of-date structural intelligence.")
    if unresolved:
        gaps.append(f"{len(unresolved)} provider references remain unresolved.")
    if history_missing:
        gaps.append(f"{len(history_missing)} repositories have no usable history.")
    if health["coverage"]["status"] == "not_scanned":
        gaps.append("Coverage has not been generated.")
    if health["security"]["status"] == "not_scanned":
        gaps.append("A security scan has not been stored.")
    for item in failed:
        gaps.append(f"{item['service']} failed graph extraction.")
    return gaps


def _inferences(services, contracts, stale, unresolved, history_missing, health) -> list[dict[str, Any]]:
    items = []
    if stale:
        items.append({"type": "stale_intelligence_present", "statement": f"{len(stale)} services have out-of-date structural intelligence.", "evidence": ["graph"]})
    if unresolved:
        items.append({"type": "unresolved_topology_present", "statement": f"{len(unresolved)} provider references are unresolved.", "evidence": ["topology"]})
    if history_missing:
        items.append({"type": "history_coverage_incomplete", "statement": f"{len(history_missing)} repositories have no usable history.", "evidence": ["history"]})
    if health["coverage"]["status"] == "not_scanned":
        items.append({"type": "coverage_incomplete", "statement": "Coverage data has not been generated.", "evidence": ["health"]})
    widest = max(services, key=lambda item: item["downstream"], default=None)
    if widest and widest["downstream"]:
        items.append({"type": "dependency_concentration", "statement": f"{widest['service']} has the widest confirmed downstream reach ({widest['downstream']} consumers).", "evidence": ["relationships"]})
    multi = [item for item in contracts["table"] if len(item["consumers"]) > 1]
    if multi:
        items.append({"type": "multi_consumer_contract", "statement": f"{multi[0]['contract']} is consumed by {len(multi[0]['consumers'])} services.", "evidence": ["contracts"]})
    return items[:8]


def _readable_unresolved(unresolved: list[dict[str, Any]]) -> dict[str, Any] | None:
    for item in unresolved:
        name = str(item.get("name") or "").strip()
        if name and not _PLACEHOLDER.match(name):
            return item
    return unresolved[0] if unresolved else None


def _recommendations(stale, unresolved, contracts, health, history_missing) -> list[dict[str, Any]]:
    items = []
    for service in stale[:3]:
        items.append({"title": f"Refresh {service} intelligence", "reason": "The structural graph is out of date.", "service": service})
    example = _readable_unresolved(unresolved)
    if example:
        name = str(example.get("name") or "").strip()
        if name and not _PLACEHOLDER.match(name):
            items.append({"title": f"Resolve {name}", "reason": "The reference is not mapped to an analyzed service.", "service": example.get("service")})
        else:
            items.append({"title": "Resolve unresolved provider references", "reason": f"{len(unresolved)} provider references are not mapped to an analyzed service.", "service": example.get("service")})
    widest = max(contracts["table"], key=lambda item: len(item["consumers"]), default=None)
    if widest and widest["consumers"]:
        items.append({"title": f"Coordinate {widest['contract']} changes", "reason": f"{len(widest['consumers'])} known consumers depend on {widest['provider']}.", "service": widest["provider"]})
    if health["coverage"]["status"] == "not_scanned" and history_missing:
        items.append({"title": f"Generate coverage for {history_missing[0]}", "reason": "Coverage has not been scanned, so verification context is incomplete.", "service": history_missing[0]})
    elif health["coverage"]["status"] == "not_scanned":
        items.append({"title": "Generate coverage", "reason": "Coverage has not been scanned.", "service": None})
    return items[:6]


def _limitations(stale, unresolved, history_missing, health, failed, relation_payload) -> list[str]:
    items = [
        "This report describes published intelligence. It is not a Boundary Gate decision.",
        "Historical change association is not a structural dependency.",
        "Automatic protobuf, gRPC, and broker discovery are not claimed.",
        "Nami Rank is not executed for this report.",
    ]
    for service in stale:
        items.append(f"{service} graph is out of date.")
    for service in history_missing:
        items.append(f"Historical data was unavailable for {service}.")
    if unresolved:
        items.append(f"{len(unresolved)} provider references could not be resolved.")
    if health["coverage"]["status"] == "not_scanned":
        items.append("Coverage has not been generated.")
    for item in failed:
        items.append(f"{item['service']} failed graph extraction: {item['error']}")
    if relation_payload.get("truncated"):
        items.append("Relationship rows in this report are truncated. The total count is still recorded.")
    return items


def _narrative(services, rows, contracts, unresolved, stale, history_ready) -> str:
    if not services:
        return "No published service intelligence is available."
    lead = f"The workspace contains {len(services)} analyzed services"
    if rows:
        lead += f" and {len(rows)} listed cross-service relationships"
    lead += "."
    bits = [lead]
    widest = max(services, key=lambda item: item["downstream"], default=None)
    if widest and widest["downstream"]:
        bits.append(f"{widest['service']} has the broadest confirmed downstream reach.")
    if stale:
        bits.append(f"{len(stale)} services contain out-of-date structural intelligence.")
    if unresolved:
        bits.append(f"{len(unresolved)} provider references remain unresolved, so impact completeness is not yet full.")
    bits.append(f"{history_ready} repositories have usable history.")
    if contracts["count"]:
        bits.append(f"{contracts['count']} cross-service contracts are recorded.")
    return " ".join(bits)
