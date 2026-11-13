"""Service cluster membership.

When a published snapshot carries clusters declared in repos.txt, that
membership is authoritative. Confirmed relationships stay visible, including
cross-cluster calls, and they do not move a service into another cluster.

Without a declaration, membership is the connected component of confirmed
cross-service relationships. Direction is ignored for that fallback and kept
on each displayed relationship. Temporal association, Nami Rank, model output,
naming similarity, and unresolved symbols do not create membership. A single
service is standalone, not a cluster.
"""

from __future__ import annotations

import hashlib
import os
from typing import Any

from dep_intel.graph_models import CONSUMED_BY, CONSUMES_GRPC, CONSUMES_HTTP, PRODUCES_EVENT, Snapshot

CONTRACT_CATEGORIES = {"http_contract", "grpc_contract", "event_contract"}
CROSS_RELATIONS = {
    CONSUMES_HTTP: "http",
    CONSUMES_GRPC: "grpc",
    PRODUCES_EVENT: "event",
    CONSUMED_BY: "event",
}


def confirmed_relationships(snapshot: Snapshot | None, *, service: str = "", kind: str = "") -> list[dict[str, Any]]:
    """Every confirmed cross-service contract edge. No presentation cap."""
    if snapshot is None:
        return []
    rows = []
    for edge in snapshot.edges:
        relation_kind = CROSS_RELATIONS.get(edge.relation)
        if relation_kind is None:
            continue
        if kind and relation_kind != kind:
            continue
        source = snapshot.nodes.get(edge.source)
        target = snapshot.nodes.get(edge.target)
        if source is None or target is None:
            continue
        caller, contract = _caller_and_contract(edge.relation, source, target)
        if caller is None or contract is None or caller.service_id == contract.service_id:
            continue
        if service and service not in {caller.service_id, contract.service_id}:
            continue
        file = caller.file_path or str(edge.evidence.get("file") or "")
        if file.startswith("/") or ".." in file.split("/"):
            file = ""
        status = str(edge.evidence.get("status") or "")
        resolution = str(edge.evidence.get("resolution") or "")
        rows.append({
            "from_service": caller.service_id,
            "to_service": contract.service_id,
            "kind": relation_kind,
            "contract_id": contract.key,
            "contract": contract.name or contract.qualified_name,
            "contract_identity": str(contract.metadata.get("identity") or contract.qualified_name or ""),
            "caller_symbol": caller.name or "",
            "caller_file": file,
            "caller_line": caller.line_start,
            "caller_qualified": caller.qualified_name or "",
            "evidence": status,
            "resolution": resolution,
            "provenance": _provenance(status),
            "cross_cluster": _crosses_cluster(snapshot, caller.service_id, contract.service_id),
        })
    rows.sort(key=lambda item: (item["from_service"], item["to_service"], item["kind"], item["contract"], item["caller_symbol"]))
    return rows


DECLARED_MEMBERSHIP = "Declared in repos.txt"
INFERRED_MEMBERSHIP = "Derived from confirmed cross-service relationships"


def membership_sentence(source: str) -> str:
    if source == "declared":
        return f"Membership source: {DECLARED_MEMBERSHIP}."
    return f"Membership source: {INFERRED_MEMBERSHIP}."


def standalone_sentence(source: str) -> str:
    if source == "declared":
        return "A standalone repository is outside the clusters declared in repos.txt."
    return "A standalone repository or service has no confirmed cross-service relationship in this stored snapshot."


def detect_service_clusters(snapshot: Snapshot | None, rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    if snapshot is None:
        return {"clusters": [], "standalone": [], "confirmed_calls": 0, "membership_source": "inferred"}
    edges = list(rows) if rows is not None else confirmed_relationships(snapshot)
    if snapshot.declared_clusters is not None:
        return _declared_clusters(snapshot, edges)
    return _inferred_clusters(snapshot, edges)


def _declared_clusters(snapshot: Snapshot, edges: list[dict[str, Any]]) -> dict[str, Any]:
    clusters = []
    members: set[str] = set()
    for item in snapshot.declared_clusters or []:
        services = [str(service) for service in item.get("services") or [] if service]
        member_set = set(services)
        members.update(member_set)
        internal = [
            row for row in edges
            if row.get("from_service") in member_set and row.get("to_service") in member_set
        ]
        cluster_id = str(item.get("cluster_id") or "")
        clusters.append({
            "cluster_id": cluster_id,
            "label": cluster_id,
            "service_count": len(services),
            "relationship_count": len(internal),
            "services": services,
            "graph_version": snapshot.version,
            "membership_source": "declared",
        })
    standalone = _standalone(snapshot, members)
    return {
        "clusters": clusters,
        "standalone": standalone,
        "confirmed_calls": len(edges),
        "membership_source": "declared",
    }


def _inferred_clusters(snapshot: Snapshot, edges: list[dict[str, Any]]) -> dict[str, Any]:
    parent: dict[str, str] = {}

    def find(service_id: str) -> str:
        parent.setdefault(service_id, service_id)
        if parent[service_id] != service_id:
            parent[service_id] = find(parent[service_id])
        return parent[service_id]

    def union(left: str, right: str) -> None:
        root_left, root_right = find(left), find(right)
        if root_left != root_right:
            parent[root_right] = root_left

    for row in edges:
        left = str(row.get("from_service") or "")
        right = str(row.get("to_service") or "")
        if left and right and left != right:
            union(left, right)
    grouped: dict[str, list[str]] = {}
    for service_id in parent:
        grouped.setdefault(find(service_id), []).append(service_id)
    clusters = []
    members: set[str] = set()
    for services in grouped.values():
        unique = sorted(set(services))
        if len(unique) < 2:
            continue
        members.update(unique)
        member_set = set(unique)
        internal = [row for row in edges if row.get("from_service") in member_set and row.get("to_service") in member_set]
        clusters.append({
            "cluster_id": cluster_id_for(unique),
            "label": cluster_label(unique),
            "service_count": len(unique),
            "relationship_count": len(internal),
            "services": unique,
            "graph_version": snapshot.version,
            "membership_source": "inferred",
        })
    clusters.sort(key=lambda item: (-item["service_count"], item["cluster_id"]))
    return {
        "clusters": clusters,
        "standalone": _standalone(snapshot, members),
        "confirmed_calls": len(edges),
        "membership_source": "inferred",
    }


def _standalone(snapshot: Snapshot, members: set[str]) -> list[dict[str, str]]:
    standalone = []
    for service_id in sorted(snapshot.partitions):
        if service_id in members:
            continue
        part = snapshot.partitions[service_id]
        standalone.append({"service_id": service_id, "repository": part.repository})
    return standalone


def _cluster_ids(snapshot: Snapshot | None) -> dict[str, str] | None:
    if snapshot is None or snapshot.declared_clusters is None:
        return None
    index: dict[str, str] = {}
    for item in snapshot.declared_clusters:
        cluster_id = str(item.get("cluster_id") or "")
        for service_id in item.get("services") or []:
            if service_id:
                index[str(service_id)] = cluster_id
    return index


def _crosses_cluster(snapshot: Snapshot | None, left: str, right: str) -> bool:
    index = _cluster_ids(snapshot)
    if index is None:
        return False
    return index.get(left) != index.get(right)


def get_service_cluster(snapshot: Snapshot | None, cluster_id: str) -> dict[str, Any] | None:
    for item in detect_service_clusters(snapshot)["clusters"]:
        if item["cluster_id"] == cluster_id:
            return item
    return None


def cluster_for_service(snapshot: Snapshot | None, service_id: str) -> dict[str, Any] | None:
    for item in detect_service_clusters(snapshot)["clusters"]:
        if service_id in item["services"]:
            return item
    return None


def cluster_downstream(snapshot: Snapshot | None, service_id: str) -> dict[str, Any]:
    """Confirmed consumers of a clustered service.

    A consumer calls this service, or calls another consumer of it. The walk
    stays inside the cluster. It does not invent file ranks, and a service
    outside every cluster returns an empty result.
    """
    empty = {"cluster_id": "", "services": [], "items": []}
    if snapshot is None or not service_id:
        return empty
    rows = confirmed_relationships(snapshot)
    cluster = cluster_for_service(snapshot, service_id)
    if cluster is None:
        return empty
    members = set(cluster["services"])
    incoming: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        provider = str(row.get("to_service") or "")
        consumer = str(row.get("from_service") or "")
        if provider in members and consumer in members and provider != consumer:
            incoming.setdefault(provider, []).append(row)
    seen = {service_id}
    queue = [service_id]
    items: list[dict[str, Any]] = []
    while queue:
        current = queue.pop(0)
        for row in incoming.get(current, []):
            consumer = str(row.get("from_service") or "")
            if not consumer or consumer in seen:
                continue
            seen.add(consumer)
            direct = current == service_id
            items.append({
                "service_id": consumer,
                "direct": direct,
                "via": "" if direct else current,
                "contract": row.get("contract") or "",
                "kind": row.get("kind") or "",
                "caller_symbol": row.get("caller_symbol") or "",
                "caller_file": row.get("caller_file") or "",
                "provenance": row.get("provenance") or "",
            })
            queue.append(consumer)
    items.sort(key=lambda item: (not item["direct"], item["service_id"]))
    return {
        "cluster_id": cluster["cluster_id"],
        "services": [item["service_id"] for item in items],
        "items": items,
    }


def cluster_id_for(services: list[str]) -> str:
    canonical = "\n".join(sorted(services))
    digest = hashlib.sha256(canonical.encode()).hexdigest()[:12]
    return f"svc-cluster-{digest}"


def cluster_label(services: list[str]) -> str:
    names = sorted(services)
    if len(names) < 2:
        return f"{len(names)}-service connected group"
    prefix = os.path.commonprefix(names)
    if "-" in prefix:
        stem = prefix[: prefix.rfind("-")]
        if len(stem) >= 2 and all(name == stem or name.startswith(stem + "-") for name in names) and any(name != stem for name in names):
            return f"{stem.upper()} service cluster"
    return f"{len(names)}-service connected group"


def _caller_and_contract(relation: str, source: Any, target: Any) -> tuple[Any, Any]:
    if relation == CONSUMED_BY:
        caller, contract = target, source
    else:
        caller, contract = source, target
    if contract.category not in CONTRACT_CATEGORIES and caller.category in CONTRACT_CATEGORIES:
        caller, contract = contract, caller
    if contract.category not in CONTRACT_CATEGORIES:
        return None, None
    return caller, contract


def _provenance(status: str) -> str:
    key = (status or "").strip().lower()
    names = {
        "extracted": "extracted",
        "extractor": "extracted",
        "declared": "declared",
        "manifest": "declared",
        "openapi": "OpenAPI",
        "generated": "generated topology",
        "override": "override",
        "manual": "override",
    }
    return names.get(key, key or "source extraction")
