"""Relation-specific maximum-score structural impact.

Stored dependency direction is not the traversal direction. A callee change
reaches callers by walking call edges backward. Scores are a maximum product
of weights, not a sum and not a probability.
"""

from __future__ import annotations

import heapq
import time
import uuid
from dataclasses import dataclass

from dep_intel.cluster_manifest import ImpactSettings
from dep_intel.graph_models import (
    CONSUMED_BY,
    CONSUMES_GRPC,
    CONSUMES_HTTP,
    EVENT_CONTRACT,
    FILE_ANCHOR,
    GRPC_CONTRACT,
    HANDLED_BY,
    HTTP_CONTRACT,
    IMPLEMENTED_BY,
    PRODUCES_EVENT,
    SCORE_MEANING,
    AffectedNode,
    Diagnostic,
    GraphNode,
    ImpactResult,
    PathStep,
    Snapshot,
)

SUPPORTED_OPERATIONAL = {"http_unavailable", "grpc_unavailable"}
UNSUPPORTED_OPERATIONAL = {
    "event_consumer_unavailable",
    "event_producer_unavailable",
    "backpressure",
    "partition_loss",
}
_COMPILE_CACHE: dict[int, tuple] = {}


LIMITATIONS = [
    SCORE_MEANING,
    "Automatic protobuf/gRPC discovery is unsupported; only manifest-declared gRPC bindings are traversed.",
    "Automatic Kafka or other broker discovery is unsupported; only manifest-declared event bindings are traversed.",
    "Event scores are a configurable structural ranking. They are not evidence that asynchronous delivery is safe.",
    "Event schema impact does not model consumer availability, producer availability, or backpressure.",
]


@dataclass(frozen=True)
class _Policy:
    direction: str
    weight: float
    reason: str


@dataclass
class _Transition:
    target: str
    weight: float
    relation: str
    direction: str
    reason: str
    evidence: dict
    confidence: float | None


@dataclass
class _Best:
    score: float
    hops: int
    path: tuple[PathStep, ...]
    confidence: float | None


def analyze(
    snapshot: Snapshot,
    roots: list[str],
    *,
    mode: str,
    settings: ImpactSettings,
    scenario: str = "",
    cancel_event=None,
    topology: str = "current",
    previous_version: str | None = None,
) -> ImpactResult:
    diagnostics: list[Diagnostic] = []
    if mode not in {"code_change", "contract_change", "operational_failure"}:
        return _empty(snapshot, roots, mode, settings, topology, previous_version, "unsupported mode")
    if mode == "operational_failure" and scenario not in SUPPORTED_OPERATIONAL:
        diagnostics.append(
            Diagnostic(
                code="unsupported_operational",
                message=(
                    f"operational scenario {scenario or '(missing)'!r} is not supported. "
                    f"Supported: {', '.join(sorted(SUPPORTED_OPERATIONAL))}. "
                    f"Unsupported: {', '.join(sorted(UNSUPPORTED_OPERATIONAL))}."
                ),
                severity="error",
            )
        )
        result = _empty(snapshot, roots, mode, settings, topology, previous_version, "")
        result.diagnostics = diagnostics
        return result
    transitions, tests_of, excluded = _compiled(snapshot, mode, scenario, settings)
    alive = [root for root in roots if root in snapshot.nodes]
    for root in roots:
        if root not in snapshot.nodes:
            diagnostics.append(Diagnostic(code="missing_root", message=f"Root {root} is not in this snapshot", severity="error"))
    best, truncated, reason, test_keys = _search(snapshot, alive, transitions, tests_of, settings, cancel_event)
    affected = [_affected(snapshot.nodes[key], item) for key, item in best.items()]
    affected.sort(key=lambda item: (-item.score, item.service_id, item.file_path, item.line_start or 0, item.qualified_name, item.key))
    tests = []
    for key in sorted(test_keys):
        node = snapshot.nodes.get(key)
        if node is None:
            continue
        tests.append(
            {
                "key": node.key,
                "service_id": node.service_id,
                "name": node.name,
                "file_path": node.file_path,
                "line_start": node.line_start,
                "role": "validation_target",
            }
        )
    contracts = [
        {
            "key": item.key,
            "service_id": item.service_id,
            "name": item.name,
            "category": item.category,
            "structural_score": item.score,
            "identity": snapshot.nodes[item.key].metadata.get("identity"),
        }
        for item in affected
        if item.category in {HTTP_CONTRACT, GRPC_CONTRACT, EVENT_CONTRACT}
    ]
    return ImpactResult(
        analysis_id=str(uuid.uuid4()),
        graph_version=snapshot.version,
        previous_version=previous_version,
        mode=mode,
        roots=list(roots),
        affected=affected,
        tests=tests,
        contracts=contracts,
        excluded=excluded,
        diagnostics=diagnostics,
        truncated=truncated,
        truncation_reason=reason,
        limitations=list(LIMITATIONS),
        topology=topology,
        budget={
            "threshold": settings.threshold,
            "epsilon": settings.epsilon,
            "max_hops": settings.max_hops,
            "max_nodes": settings.max_nodes,
            "max_edges_examined": settings.max_edges_examined,
            "timeout_ms": settings.timeout_ms,
        },
    )


def _compiled(snapshot: Snapshot, mode: str, scenario: str, settings: ImpactSettings):
    weights = settings.weights
    key = (
        mode,
        scenario,
        weights.internal_dependency,
        weights.http_contract,
        weights.grpc_contract,
        weights.event_contract,
        settings.min_edge_confidence,
        settings.include_unknown_confidence,
        len(snapshot.edges),
    )
    cache_id = id(snapshot)
    edges_id = id(snapshot.edges)
    cached = _COMPILE_CACHE.get(cache_id)
    if cached is not None and cached[0] == key and cached[1] == edges_id:
        return cached[2]
    compiled = _compile(snapshot, _policies(mode, scenario, settings), settings)
    if len(_COMPILE_CACHE) > 8:
        _COMPILE_CACHE.clear()
    _COMPILE_CACHE[cache_id] = (key, edges_id, compiled)
    return compiled


def _policies(mode: str, scenario: str, settings: ImpactSettings) -> dict[str, _Policy]:
    internal = settings.weights.internal_dependency
    http = settings.weights.http_contract
    grpc = settings.weights.grpc_contract
    event = settings.weights.event_contract
    structural = _Policy("reverse", 1.0, "structural binding")
    if mode == "operational_failure" and scenario == "http_unavailable":
        return {
            "CALLS": _Policy("reverse", internal, "caller depends on the changed callee"),
            HANDLED_BY: structural,
            CONSUMES_HTTP: _Policy("reverse", http, "client depends on the HTTP contract"),
            "TESTED_BY": _Policy("test", 1.0, "validation target"),
        }
    if mode == "operational_failure" and scenario == "grpc_unavailable":
        return {
            "CALLS": _Policy("reverse", internal, "caller depends on the changed callee"),
            IMPLEMENTED_BY: structural,
            CONSUMES_GRPC: _Policy("reverse", grpc, "client depends on the gRPC contract"),
            "TESTED_BY": _Policy("test", 1.0, "validation target"),
        }
    policies = {
        "CALLS": _Policy("reverse", internal, "caller depends on the changed callee"),
        "IMPORTS_FROM": _Policy("reverse", internal, "importer depends on the imported module"),
        "INHERITS": _Policy("reverse", internal, "subtype depends on the base type"),
        "REFERENCES": _Policy("reverse", internal, "referencer depends on the referenced symbol"),
        "CONTAINS": _Policy("seed", 1.0, "file input seeds contained symbols"),
        "TESTED_BY": _Policy("test", 1.0, "validation target"),
        HANDLED_BY: structural,
        IMPLEMENTED_BY: structural,
        CONSUMES_HTTP: _Policy("reverse", http, "client depends on the HTTP contract"),
        CONSUMES_GRPC: _Policy("reverse", grpc, "client depends on the gRPC contract"),
        PRODUCES_EVENT: _Policy("forward", 1.0, "producer binding reaches the event contract"),
        CONSUMED_BY: _Policy("forward", event, "event schema reaches a consumer"),
    }
    if mode == "contract_change":
        policies.pop(HANDLED_BY, None)
        policies.pop(IMPLEMENTED_BY, None)
    return policies


def _compile(snapshot: Snapshot, policies: dict[str, _Policy], settings: ImpactSettings):
    grouped: dict[tuple[str, str, str, str], _Transition] = {}
    tests_of: dict[str, list[str]] = {}
    excluded: list[dict] = []
    contains: dict[str, list[str]] = {}
    for edge in snapshot.edges:
        policy = policies.get(edge.relation)
        if policy is None:
            continue
        confidence = edge.evidence.get("confidence")
        if isinstance(confidence, (int, float)):
            if settings.min_edge_confidence is not None and confidence < settings.min_edge_confidence:
                excluded.append({**_edge_ref(edge), "reason": "below_min_confidence"})
                continue
        elif not settings.include_unknown_confidence:
            excluded.append({**_edge_ref(edge), "reason": "unknown_confidence"})
            continue
        if policy.direction == "test":
            tests_of.setdefault(edge.source, []).append(edge.target)
            continue
        if policy.direction == "seed":
            contains.setdefault(edge.source, []).append(edge.target)
            continue
        if policy.direction == "reverse":
            origin, dest = edge.target, edge.source
        else:
            origin, dest = edge.source, edge.target
        key = (origin, dest, edge.relation, policy.direction)
        candidate = _Transition(
            target=dest,
            weight=policy.weight,
            relation=edge.relation,
            direction=policy.direction,
            reason=policy.reason,
            evidence=edge.evidence,
            confidence=float(confidence) if isinstance(confidence, (int, float)) else None,
        )
        current = grouped.get(key)
        if current is None or _evidence_rank(candidate.evidence) > _evidence_rank(current.evidence):
            grouped[key] = candidate
    adjacency: dict[str, list[_Transition]] = {}
    for (origin, _dest, _relation, _direction), transition in grouped.items():
        adjacency.setdefault(origin, []).append(transition)
    for origin, targets in contains.items():
        adjacency.setdefault(origin, [])
        for target in targets:
            adjacency[origin].append(
                _Transition(
                    target,
                    1.0,
                    "CONTAINS",
                    "forward",
                    "file input seeds contained symbols",
                    {"status": "extracted", "source": "crg"},
                    None,
                )
            )
    return adjacency, tests_of, excluded


def _search(snapshot, roots, adjacency, tests_of, settings: ImpactSettings, cancel_event):
    epsilon = settings.epsilon
    frontier: dict[str, list[tuple[float, int]]] = {}
    best: dict[str, _Best] = {}
    heap: list[tuple] = []
    counter = 0
    deadline = time.monotonic() + (settings.timeout_ms / 1000)
    truncated = False
    reason = ""
    examined = 0
    test_keys: set[str] = set()
    chain: list[tuple[int | None, PathStep | None]] = []

    def consider(
        node: str,
        score: float,
        hops: int,
        step: PathStep | None,
        confidence: float | None,
        *,
        parent: int | None = None,
        seeded: bool = False,
    ):
        nonlocal counter
        if not seeded and score < settings.threshold:
            return None
        states = frontier.get(node, [])
        if _dominated(states, score, hops, epsilon):
            return None
        if node not in frontier and len(frontier) >= settings.max_nodes:
            return "max_nodes"
        frontier[node] = _insert(states, score, hops, epsilon)
        index = len(chain)
        chain.append((parent, step))
        current = best.get(node)
        if current is None or _might_improve(score, hops, current, epsilon):
            path = _walk(chain, index)
            if current is None or _better(score, hops, path, current, epsilon):
                best[node] = _Best(score, hops, path, confidence)
        counter += 1
        heapq.heappush(heap, (-score, hops, counter, node, index, confidence))
        return None

    for root in roots:
        node = snapshot.nodes[root]
        consider(root, 1.0, 0, None, None, seeded=True)
        if node.category == FILE_ANCHOR or node.kind == "File":
            for transition in adjacency.get(root, []):
                if transition.relation == "CONTAINS":
                    consider(transition.target, 1.0, 1, _step(root, transition), None, seeded=True)

    while heap:
        if cancel_event is not None and cancel_event.is_set():
            truncated, reason = True, "cancelled"
            break
        if time.monotonic() >= deadline:
            truncated, reason = True, "timeout"
            break
        score_neg, hops, _count, node, parent, confidence = heapq.heappop(heap)
        score = -score_neg
        if not _contains_state(frontier.get(node, []), score, hops, epsilon):
            continue
        for target in tests_of.get(node, []):
            test_keys.add(target)
        if hops >= settings.max_hops:
            continue
        for transition in adjacency.get(node, []):
            if transition.relation == "CONTAINS":
                continue
            examined += 1
            if examined > settings.max_edges_examined:
                truncated, reason = True, "max_edges_examined"
                break
            stop = consider(
                transition.target,
                score * transition.weight,
                hops + 1,
                _step(node, transition),
                _merge_confidence(confidence, transition.confidence),
                parent=parent,
            )
            if stop:
                truncated, reason = True, stop
                break
        if truncated:
            break
    return best, truncated, reason, test_keys


def _might_improve(score: float, hops: int, current: _Best, epsilon: float) -> bool:
    if score > current.score + epsilon:
        return True
    return abs(score - current.score) <= epsilon and hops <= current.hops


def _walk(chain: list[tuple[int | None, PathStep | None]], index: int | None) -> tuple[PathStep, ...]:
    steps: list[PathStep] = []
    seen = 0
    while index is not None:
        parent, step = chain[index]
        if step is not None:
            steps.append(step)
        index = parent
        seen += 1
        if seen > len(chain):
            break
    steps.reverse()
    return tuple(steps)


def _dominated(states: list[tuple[float, int]], score: float, hops: int, epsilon: float) -> bool:
    return any(existing_score + epsilon >= score and existing_hops <= hops for existing_score, existing_hops in states)


def _contains_state(states: list[tuple[float, int]], score: float, hops: int, epsilon: float) -> bool:
    return any(abs(existing_score - score) <= epsilon and existing_hops == hops for existing_score, existing_hops in states)


def _insert(states: list[tuple[float, int]], score: float, hops: int, epsilon: float) -> list[tuple[float, int]]:
    kept = []
    for existing_score, existing_hops in states:
        worse_or_equal_score = score + epsilon >= existing_score
        fewer_or_equal_hops = hops <= existing_hops
        strictly = score > existing_score + epsilon or hops < existing_hops
        if worse_or_equal_score and fewer_or_equal_hops and strictly:
            continue
        kept.append((existing_score, existing_hops))
    kept.append((score, hops))
    return kept


def _better(score: float, hops: int, path: tuple[PathStep, ...], current: _Best, epsilon: float) -> bool:
    if score > current.score + epsilon:
        return True
    if abs(score - current.score) <= epsilon:
        if hops < current.hops:
            return True
        if hops == current.hops:
            return _path_token(path) < _path_token(current.path)
    return False


def _path_token(path: tuple[PathStep, ...]) -> tuple:
    return tuple((step.relation, step.direction, step.target) for step in path)


def _step(origin: str, transition: _Transition) -> PathStep:
    return PathStep(
        source=origin,
        target=transition.target,
        relation=transition.relation,
        direction=transition.direction,
        weight=transition.weight,
        reason=transition.reason,
        evidence={
            "source": transition.evidence.get("source"),
            "status": transition.evidence.get("status"),
            "file": transition.evidence.get("file"),
            "line": transition.evidence.get("line"),
            "confidence": transition.confidence,
            "contract_id": transition.evidence.get("contract_id"),
            "extractor": transition.evidence.get("extractor"),
        },
    )


def _affected(node: GraphNode, best: _Best) -> AffectedNode:
    return AffectedNode(
        key=node.key,
        score=best.score,
        service_id=node.service_id,
        name=node.name,
        qualified_name=node.qualified_name,
        file_path=node.file_path,
        line_start=node.line_start,
        category=node.category,
        kind=node.kind,
        confidence=best.confidence,
        path=list(best.path),
        hops=best.hops,
    )


def _merge_confidence(left: float | None, right: float | None) -> float | None:
    if left is None:
        return right
    if right is None:
        return left
    return min(left, right)


def _evidence_rank(evidence: dict) -> tuple:
    confidence = evidence.get("confidence")
    score = float(confidence) if isinstance(confidence, (int, float)) else -1.0
    status = 1 if evidence.get("status") == "extracted" else 0
    return (status, score)


def _edge_ref(edge) -> dict:
    return {
        "source": edge.source,
        "target": edge.target,
        "relation": edge.relation,
        "confidence": edge.evidence.get("confidence"),
        "status": edge.evidence.get("status"),
    }


def _empty(snapshot, roots, mode, settings, topology, previous_version, reason: str) -> ImpactResult:
    return ImpactResult(
        analysis_id=str(uuid.uuid4()),
        graph_version=snapshot.version,
        previous_version=previous_version,
        mode=mode,
        roots=list(roots),
        affected=[],
        tests=[],
        contracts=[],
        excluded=[],
        diagnostics=[Diagnostic(code="rejected", message=reason, severity="error")] if reason else [],
        truncated=False,
        truncation_reason="",
        limitations=list(LIMITATIONS),
        topology=topology,
        budget={"timeout_ms": settings.timeout_ms, "max_hops": settings.max_hops},
    )
