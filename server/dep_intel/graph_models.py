"""Typed graph identities, snapshots, and impact results.

Symbol keys are digests of a canonical JSON tuple. They do not use CRG row
ids and they do not join fields with a delimiter that can collide.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any


EXTRACTOR_VERSION = "nami-contract-1"
SCORE_KIND = "structural_impact"
SCORE_MEANING = (
    "Maximum product of relation weights along the strongest structural path. "
    "This is not a probability of breakage."
)

CODE_SYMBOL = "code_symbol"
HTTP_CONTRACT = "http_contract"
GRPC_CONTRACT = "grpc_contract"
EVENT_CONTRACT = "event_contract"
UNRESOLVED = "unresolved"
FILE_ANCHOR = "file_anchor"
HANDLER_ANCHOR = "handler_anchor"

CONSUMES_HTTP = "CONSUMES_HTTP"
HANDLED_BY = "HANDLED_BY"
CONSUMES_GRPC = "CONSUMES_GRPC"
IMPLEMENTED_BY = "IMPLEMENTED_BY"
PRODUCES_EVENT = "PRODUCES_EVENT"
CONSUMED_BY = "CONSUMED_BY"


def symbol_key(service_id: str, rel_path: str, qualified: str, kind: str) -> str:
    payload = json.dumps(
        [service_id, rel_path, qualified, kind],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def fingerprint_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class Diagnostic:
    code: str
    message: str
    service_id: str = ""
    file: str = ""
    line: int | None = None
    severity: str = "warning"

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "service_id": self.service_id,
            "file": self.file,
            "line": self.line,
            "severity": self.severity,
        }


@dataclass
class GraphNode:
    key: str
    category: str
    service_id: str
    name: str
    qualified_name: str
    file_path: str
    line_start: int | None = None
    line_end: int | None = None
    language: str = ""
    kind: str = ""
    crg_id: int | None = None
    span_hash: str | None = None
    bounds_verified: bool = False
    is_test: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "category": self.category,
            "service_id": self.service_id,
            "name": self.name,
            "qualified_name": self.qualified_name,
            "file_path": self.file_path,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "language": self.language,
            "kind": self.kind,
            "crg_id": self.crg_id,
            "span_hash": self.span_hash,
            "bounds_verified": self.bounds_verified,
            "is_test": self.is_test,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "GraphNode":
        return cls(
            key=payload["key"],
            category=payload["category"],
            service_id=payload["service_id"],
            name=payload["name"],
            qualified_name=payload["qualified_name"],
            file_path=payload.get("file_path") or "",
            line_start=payload.get("line_start"),
            line_end=payload.get("line_end"),
            language=payload.get("language") or "",
            kind=payload.get("kind") or "",
            crg_id=payload.get("crg_id"),
            span_hash=payload.get("span_hash"),
            bounds_verified=bool(payload.get("bounds_verified")),
            is_test=bool(payload.get("is_test")),
            metadata=dict(payload.get("metadata") or {}),
        )


@dataclass
class GraphEdge:
    source: str
    target: str
    relation: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "relation": self.relation,
            "evidence": self.evidence,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "GraphEdge":
        return cls(
            source=payload["source"],
            target=payload["target"],
            relation=payload["relation"],
            evidence=dict(payload.get("evidence") or {}),
        )


@dataclass
class Partition:
    service_id: str
    repository: str
    nodes: dict[str, GraphNode] = field(default_factory=dict)
    edges: list[GraphEdge] = field(default_factory=list)
    quality: str = "ok"
    error: str = ""
    schema_version: str = ""
    package_version: str = ""
    observed_at: str = ""
    index_revision: str | None = None
    workspace_head: str | None = None
    stabilization: str = "none"
    db_identity: str = ""
    file_hashes: dict[str, str] = field(default_factory=dict)
    diagnostics: list[Diagnostic] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "service_id": self.service_id,
            "repository": self.repository,
            "nodes": [node.to_dict() for node in self.nodes.values()],
            "edges": [edge.to_dict() for edge in self.edges],
            "quality": self.quality,
            "error": self.error,
            "schema_version": self.schema_version,
            "package_version": self.package_version,
            "observed_at": self.observed_at,
            "index_revision": self.index_revision,
            "workspace_head": self.workspace_head,
            "stabilization": self.stabilization,
            "db_identity": self.db_identity,
            "file_hashes": self.file_hashes,
            "diagnostics": [item.to_dict() for item in self.diagnostics],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Partition":
        nodes = [GraphNode.from_dict(item) for item in payload.get("nodes") or []]
        return cls(
            service_id=payload["service_id"],
            repository=payload.get("repository") or "",
            nodes={node.key: node for node in nodes},
            edges=[GraphEdge.from_dict(item) for item in payload.get("edges") or []],
            quality=payload.get("quality") or "ok",
            error=payload.get("error") or "",
            schema_version=str(payload.get("schema_version") or ""),
            package_version=payload.get("package_version") or "",
            observed_at=payload.get("observed_at") or "",
            index_revision=payload.get("index_revision"),
            workspace_head=payload.get("workspace_head"),
            stabilization=payload.get("stabilization") or "none",
            db_identity=payload.get("db_identity") or "",
            file_hashes=dict(payload.get("file_hashes") or {}),
            diagnostics=[Diagnostic(**item) for item in payload.get("diagnostics") or []],
        )


@dataclass
class Snapshot:
    version: str
    generated_at: str
    partitions: dict[str, Partition]
    nodes: dict[str, GraphNode]
    edges: list[GraphEdge]
    forward: dict[str, list[int]]
    reverse: dict[str, list[int]]
    by_service: dict[str, list[str]]
    by_file: dict[tuple[str, str], list[str]]
    by_name: dict[tuple[str, str], list[str]]
    contracts: dict[str, str]
    revision_vector: dict[str, dict[str, Any]]
    diagnostics: list[Diagnostic] = field(default_factory=list)
    bridge_fingerprints: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "generated_at": self.generated_at,
            "partitions": {key: part.to_dict() for key, part in self.partitions.items()},
            "edges": [edge.to_dict() for edge in self.edges if edge.evidence.get("injected")],
            "diagnostics": [item.to_dict() for item in self.diagnostics],
            "bridge_fingerprints": self.bridge_fingerprints,
            "revision_vector": self.revision_vector,
        }

    def summary(self) -> dict[str, Any]:
        unresolved = sum(1 for node in self.nodes.values() if node.category == UNRESOLVED)
        contracts = sum(1 for node in self.nodes.values() if node.category in {HTTP_CONTRACT, GRPC_CONTRACT, EVENT_CONTRACT})
        stale = [part.service_id for part in self.partitions.values() if part.quality != "ok"]
        return {
            "version": self.version,
            "generated_at": self.generated_at,
            "services": [
                {
                    "id": part.service_id,
                    "repository": part.repository,
                    "quality": part.quality,
                    "error": part.error,
                    "symbols": sum(1 for node in part.nodes.values() if node.category == CODE_SYMBOL),
                    "schema_version": part.schema_version,
                    "package_version": part.package_version,
                    "index_revision": part.index_revision,
                    "workspace_head": part.workspace_head,
                    "stabilization": part.stabilization,
                    "observed_at": part.observed_at,
                }
                for part in self.partitions.values()
            ],
            "node_count": len(self.nodes),
            "edge_count": len(self.edges),
            "contract_count": contracts,
            "unresolved_count": unresolved,
            "stale_services": stale,
            "revision_vector": self.revision_vector,
            "diagnostics": [item.to_dict() for item in self.diagnostics],
        }


@dataclass
class ChangeSet:
    old_version: str
    new_version: str
    added: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    body_only: list[str] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)
    renamed: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "old_version": self.old_version,
            "new_version": self.new_version,
            "added": self.added,
            "modified": self.modified,
            "deleted": self.deleted,
            "body_only": self.body_only,
            "stale": self.stale,
            "renamed": self.renamed,
        }


@dataclass
class PathStep:
    source: str
    target: str
    relation: str
    direction: str
    weight: float
    reason: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "relation": self.relation,
            "direction": self.direction,
            "weight": self.weight,
            "reason": self.reason,
            "evidence": self.evidence,
        }


@dataclass
class AffectedNode:
    key: str
    score: float
    service_id: str
    name: str
    qualified_name: str
    file_path: str
    line_start: int | None
    category: str
    kind: str
    confidence: float | None
    path: list[PathStep]
    hops: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "structural_score": self.score,
            "score_kind": SCORE_KIND,
            "service_id": self.service_id,
            "name": self.name,
            "qualified_name": self.qualified_name,
            "file_path": self.file_path,
            "line_start": self.line_start,
            "category": self.category,
            "kind": self.kind,
            "confidence": self.confidence,
            "hops": self.hops,
            "path": [step.to_dict() for step in self.path],
        }


@dataclass
class ImpactResult:
    analysis_id: str
    graph_version: str
    previous_version: str | None
    mode: str
    roots: list[str]
    affected: list[AffectedNode]
    tests: list[dict[str, Any]]
    contracts: list[dict[str, Any]]
    excluded: list[dict[str, Any]]
    diagnostics: list[Diagnostic]
    truncated: bool
    truncation_reason: str
    limitations: list[str]
    topology: str
    budget: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "analysis_id": self.analysis_id,
            "graph_version": self.graph_version,
            "previous_version": self.previous_version,
            "mode": self.mode,
            "topology": self.topology,
            "score_kind": SCORE_KIND,
            "score_meaning": SCORE_MEANING,
            "roots": self.roots,
            "affected": [item.to_dict() for item in self.affected],
            "tests": self.tests,
            "contracts": self.contracts,
            "excluded": self.excluded,
            "diagnostics": [item.to_dict() for item in self.diagnostics],
            "truncated": self.truncated,
            "truncation_reason": self.truncation_reason,
            "limitations": self.limitations,
            "budget": self.budget,
        }
