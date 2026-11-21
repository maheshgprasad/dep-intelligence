"""Connected components of confirmed cross-service relationships.

Membership ignores edge direction. Displayed relationships keep their direction.
Temporal association, Nami Rank, model output, naming similarity, and unresolved
symbols do not create membership. A single service is standalone, not a cluster.
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
        from_cluster, to_cluster = _declared_pair(snapshot, caller.service_id, contract.service_id)
        rows.append({
            "from_service": caller.service_id,
            "to_service": contract.service_id,
            "from_cluster": from_cluster,
            "to_cluster": to_cluster,
            "cross_cluster": bool(from_cluster and to_cluster and from_cluster != to_cluster),
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
        })
    rows.sort(key=lambda item: (item["from_service"], item["to_service"], item["kind"], item["contract"], item["caller_symbol"]))
    return rows


def detect_service_clusters(snapshot: Snapshot | None, rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    if snapshot is None:
        return {"clusters": [], "standalone": [], "confirmed_calls": 0, "membership_source": "derived"}
    edges = list(rows) if rows is not None else confirmed_relationships(snapshot)
    declared = [item for item in (getattr(snapshot, "declared_clusters", None) or []) if isinstance(item, dict) and item.get("id")]
    if declared:
        return _declared_membership(snapshot, declared, edges)
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
            "membership_source": "derived",
        })
    clusters.sort(key=lambda item: (-item["service_count"], item["cluster_id"]))
    standalone = []
    for service_id in sorted(snapshot.partitions):
        if service_id in members:
            continue
        part = snapshot.partitions[service_id]
        standalone.append({"service_id": service_id, "repository": part.repository})
    return {"clusters": clusters, "standalone": standalone, "confirmed_calls": len(edges), "membership_source": "derived"}


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


def cluster_downstream(snapshot: Snapshot | None, service_id: str, cluster_id: str = "") -> dict[str, Any]:
    """Confirmed consumers of a clustered service.

    A consumer calls this service, or calls another consumer of it. The walk
    stays inside the cluster. It does not invent file ranks, and a service
    outside every cluster returns an empty result. An explicit cluster id
    selects that membership; confirmed calls do not add or remove members.
    """
    empty = {"cluster_id": "", "services": [], "items": []}
    if snapshot is None or not service_id:
        return empty
    rows = confirmed_relationships(snapshot)
    if cluster_id:
        selected = get_service_cluster(snapshot, cluster_id)
        if selected is None or service_id not in set(selected.get("services") or []):
            return empty
        cluster = selected
    else:
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
    external = _external_targets(rows, service_id, members, cluster["cluster_id"])
    body = {
        "cluster_id": cluster["cluster_id"],
        "services": [item["service_id"] for item in items],
        "items": items,
    }
    if external:
        body["external"] = external
    return body


def annotate_relationship_clusters(rows: list[dict[str, Any]], groups: dict[str, Any]) -> None:
    """Fill cluster ids from the membership result. Declared ids win over an empty derived label."""
    membership: dict[str, str] = {}
    for cluster in groups.get("clusters") or []:
        for service_id in cluster.get("services") or []:
            membership[str(service_id)] = str(cluster.get("cluster_id") or "")
    for row in rows:
        left = membership.get(str(row.get("from_service") or ""), "")
        right = membership.get(str(row.get("to_service") or ""), "")
        row["from_cluster"] = left
        row["to_cluster"] = right
        row["cross_cluster"] = bool(left and right and left != right)


def membership_source_label(source: str) -> str:
    if source == "declared":
        return "Declared in repos.txt"
    return "Derived from confirmed cross-service relationships"


def _declared_membership(snapshot: Snapshot, declared: list[dict[str, Any]], edges: list[dict[str, Any]]) -> dict[str, Any]:
    clusters = []
    members: set[str] = set()
    for item in sorted(declared, key=lambda row: str(row.get("id") or "")):
        services = sorted({str(service) for service in item.get("services") or [] if service})
        if len(services) < 2:
            continue
        members.update(services)
        member_set = set(services)
        internal = [
            row for row in edges
            if row.get("from_service") in member_set and row.get("to_service") in member_set
        ]
        clusters.append({
            "cluster_id": str(item.get("id") or ""),
            "label": str(item.get("id") or ""),
            "service_count": len(services),
            "relationship_count": len(internal),
            "services": services,
            "graph_version": snapshot.version,
            "membership_source": "declared",
        })
    standalone = []
    for service_id in sorted(snapshot.partitions):
        if service_id in members:
            continue
        part = snapshot.partitions[service_id]
        standalone.append({"service_id": service_id, "repository": part.repository})
    return {
        "clusters": clusters,
        "standalone": standalone,
        "confirmed_calls": len(edges),
        "membership_source": "declared",
    }


def _declared_pair(snapshot: Snapshot | None, left: str, right: str) -> tuple[str, str]:
    membership: dict[str, str] = {}
    for item in getattr(snapshot, "declared_clusters", None) or []:
        cluster_id = str(item.get("id") or "")
        for service_id in item.get("services") or []:
            membership[str(service_id)] = cluster_id
    return membership.get(left, ""), membership.get(right, "")


def _external_targets(rows: list[dict[str, Any]], service_id: str, members: set[str], cluster_id: str) -> list[dict[str, Any]]:
    external = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        provider = str(row.get("to_service") or "")
        consumer = str(row.get("from_service") or "")
        inside = {provider, consumer} & members
        outside = {provider, consumer} - members
        if not inside or not outside or service_id not in {provider, consumer}:
            continue
        other = next(iter(outside))
        token = (consumer, provider)
        if token in seen:
            continue
        seen.add(token)
        external.append({
            "service_id": other,
            "from_service": consumer,
            "to_service": provider,
            "from_cluster": row.get("from_cluster") or (cluster_id if consumer in members else ""),
            "to_cluster": row.get("to_cluster") or (cluster_id if provider in members else ""),
            "cross_cluster": True,
            "contract": row.get("contract") or "",
            "kind": row.get("kind") or "",
        })
    external.sort(key=lambda item: (item["service_id"], item["from_service"], item["to_service"]))
    return external


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
