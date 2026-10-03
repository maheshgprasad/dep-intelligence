"""Diff snapshots and choose the old, new, or union topology for a change."""

from __future__ import annotations

import uuid

from dep_intel.cross_repo_graph import index_snapshot
from dep_intel.graph_models import ChangeSet, GraphEdge, GraphNode, Snapshot


def diff_snapshots(old: Snapshot | None, new: Snapshot) -> ChangeSet:
    if old is None:
        return ChangeSet(old_version="", new_version=new.version, added=sorted(new.nodes))
    added, deleted, modified, body_only, stale = [], [], [], [], []
    for key in sorted(set(new.nodes) - set(old.nodes)):
        added.append(key)
    for key in sorted(set(old.nodes) - set(new.nodes)):
        deleted.append(key)
    for key in sorted(set(old.nodes) & set(new.nodes)):
        before, after = old.nodes[key], new.nodes[key]
        if not after.bounds_verified and before.bounds_verified:
            stale.append(key)
        structural = (before.qualified_name, before.kind, before.category, before.file_path) != (
            after.qualified_name,
            after.kind,
            after.category,
            after.file_path,
        )
        body = before.span_hash and after.span_hash and before.span_hash != after.span_hash
        if structural:
            modified.append(key)
        elif body:
            body_only.append(key)
            modified.append(key)
    return ChangeSet(
        old_version=old.version,
        new_version=new.version,
        added=added,
        modified=modified,
        deleted=deleted,
        body_only=body_only,
        stale=stale,
        renamed=[],
    )


def union_snapshot(old: Snapshot, new: Snapshot) -> Snapshot:
    """Keep callers that disappeared in the same update as the nodes they called."""
    partitions = {key: part for key, part in new.partitions.items()}
    overlay: dict[str, GraphNode] = {}
    new_partition_keys = {key for part in new.partitions.values() for key in part.nodes}
    for key, node in new.nodes.items():
        if key not in new_partition_keys:
            overlay[key] = node
    for key, node in old.nodes.items():
        if key not in new.nodes:
            overlay[key] = node
    present = {(edge.source, edge.target, edge.relation) for edge in new.edges}
    bridges = [_with_provenance(edge, "new") for edge in new.edges if edge.evidence.get("injected")]
    node_keys = set(new.nodes) | set(old.nodes)
    for edge in old.edges:
        token = (edge.source, edge.target, edge.relation)
        if token in present:
            continue
        if edge.source not in node_keys or edge.target not in node_keys:
            continue
        bridges.append(_with_provenance(edge, "old"))
    return index_snapshot(
        version=f"union:{old.version}:{new.version}",
        generated_at=new.generated_at,
        partitions=partitions,
        overlay=overlay,
        bridges=bridges,
        diagnostics=list(new.diagnostics),
        fingerprints=[],
    )


def fresh_version() -> str:
    return uuid.uuid4().hex


def _with_provenance(edge: GraphEdge, provenance: str) -> GraphEdge:
    evidence = dict(edge.evidence)
    evidence["provenance"] = provenance
    return GraphEdge(source=edge.source, target=edge.target, relation=edge.relation, evidence=evidence)
