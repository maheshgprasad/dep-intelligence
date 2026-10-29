"""Shared per-service summary for repository reports and the cluster catalogue."""

from __future__ import annotations

from typing import Any

from dep_intel.config import Settings
from dep_intel.graph_models import HTTP_CONTRACT, GRPC_CONTRACT, EVENT_CONTRACT, Snapshot
from dep_intel.reporting.collect import label

CONTRACTS = {HTTP_CONTRACT, GRPC_CONTRACT, EVENT_CONTRACT}


def service_summary(
    settings: Settings,
    snapshot: Snapshot,
    service_id: str,
    rows: list[dict[str, Any]],
    history: dict[str, Any],
    health: dict[str, Any],
) -> dict[str, Any]:
    del settings
    part = snapshot.partitions[service_id]
    symbols = sum(1 for node in part.nodes.values() if node.category == "code_symbol")
    provides = sum(1 for node in part.nodes.values() if node.category in CONTRACTS)
    downstream = sorted({row["from_service"] for row in rows if row.get("to_service") == service_id})
    upstream = sorted({row["to_service"] for row in rows if row.get("from_service") == service_id})
    graph_status = "current" if part.quality == "ok" and not part.error else "stale" if part.quality != "ok" else "unavailable"
    if part.error and part.quality == "ok":
        graph_status = "unavailable"
    health_states = [health["packages"]["status"], health["security"]["status"], health["coverage"]["status"]]
    if all(item == "not_scanned" for item in health_states):
        health_status = "not_scanned"
    elif any(item == "available" for item in health_states) and not all(item == "available" for item in health_states):
        health_status = "partial"
    elif all(item == "available" for item in health_states):
        health_status = "available"
    else:
        health_status = "unavailable"
    return {
        "service": service_id,
        "repository": part.repository,
        "language": "",
        "symbols": symbols,
        "internal_relationships": len(part.edges),
        "provides": provides,
        "consumes": len(upstream),
        "downstream": len(downstream),
        "upstream": len(upstream),
        "downstream_services": downstream,
        "upstream_services": upstream,
        "graph_status": graph_status,
        "graph_label": label(graph_status),
        "history_status": history["status"],
        "history_label": history["label"],
        "health_status": health_status,
        "health_label": label(health_status),
        "error": part.error or "",
    }
