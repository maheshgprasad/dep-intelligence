"""Repository partitions, contract bridges, and snapshot indexes."""

from __future__ import annotations

import hashlib
from typing import Any

from dep_intel.cluster_manifest import ClusterManifest, resolved_repo
from dep_intel.contracts import ContractModel, HttpCall, HttpRoute, build_contract_model
from dep_intel.graph_models import (
    CODE_SYMBOL,
    CONSUMED_BY,
    CONSUMES_GRPC,
    CONSUMES_HTTP,
    EVENT_CONTRACT,
    FILE_ANCHOR,
    GRPC_CONTRACT,
    HANDLED_BY,
    HANDLER_ANCHOR,
    HTTP_CONTRACT,
    IMPLEMENTED_BY,
    PRODUCES_EVENT,
    Diagnostic,
    GraphEdge,
    GraphNode,
    Partition,
    Snapshot,
    fingerprint_text,
    symbol_key,
)
from dep_intel.store import now


def assemble(partitions, manifest, files_by_service, *, version: str, generated_at: str | None = None) -> Snapshot:
    model = build_contract_model(manifest, files_by_service)
    annotate_bounds(partitions, files_by_service)
    overlay: dict[str, GraphNode] = {}
    bridges: list[GraphEdge] = []
    diagnostics = list(_diagnostics(model))
    http_bridges(partitions, model, overlay, bridges, diagnostics)
    grpc_bridges(partitions, manifest, overlay, bridges, diagnostics)
    event_bridges(partitions, manifest, overlay, bridges, diagnostics)
    expected_bridges(partitions, manifest, overlay, bridges, diagnostics)
    fingerprints = sorted(
        f"{edge.relation}|{edge.source}|{edge.target}|{edge.evidence.get('status')}|{edge.evidence.get('contract_id')}"
        for edge in bridges
    )
    return index_snapshot(
        version=version,
        generated_at=generated_at or now(),
        partitions=partitions,
        overlay=overlay,
        bridges=bridges,
        diagnostics=diagnostics,
        fingerprints=fingerprints,
    )


def index_snapshot(*, version, generated_at, partitions, overlay, bridges, diagnostics, fingerprints) -> Snapshot:
    nodes: dict[str, GraphNode] = {}
    edges: list[GraphEdge] = []
    for part in partitions.values():
        nodes.update(part.nodes)
        edges.extend(edge for edge in part.edges if not edge.evidence.get("injected"))
    nodes.update(overlay)
    edges.extend(bridges)
    forward: dict[str, list[int]] = {}
    reverse: dict[str, list[int]] = {}
    for index, edge in enumerate(edges):
        forward.setdefault(edge.source, []).append(index)
        reverse.setdefault(edge.target, []).append(index)
    by_service: dict[str, list[str]] = {}
    by_file: dict[tuple[str, str], list[str]] = {}
    by_name: dict[tuple[str, str], list[str]] = {}
    contracts: dict[str, str] = {}
    for node in nodes.values():
        by_service.setdefault(node.service_id, []).append(node.key)
        if node.file_path:
            by_file.setdefault((node.service_id, node.file_path), []).append(node.key)
        if node.name:
            by_name.setdefault((node.service_id, node.name), []).append(node.key)
        identity = node.metadata.get("identity")
        if isinstance(identity, str):
            contracts[f"{node.service_id}|{identity}"] = node.key
    revision = {
        part.service_id: {
            "index_revision": part.index_revision,
            "workspace_head": part.workspace_head,
            "schema_version": part.schema_version,
            "package_version": part.package_version,
            "quality": part.quality,
            "observed_at": part.observed_at,
            "stabilization": part.stabilization,
            "error": part.error,
        }
        for part in partitions.values()
    }
    return Snapshot(
        version=version,
        generated_at=generated_at,
        partitions=partitions,
        nodes=nodes,
        edges=edges,
        forward=forward,
        reverse=reverse,
        by_service=by_service,
        by_file=by_file,
        by_name=by_name,
        contracts=contracts,
        revision_vector=revision,
        diagnostics=diagnostics,
        bridge_fingerprints=fingerprints,
    )


def snapshot_from_published(payload: dict[str, Any]) -> Snapshot:
    partitions = {key: Partition.from_dict(value) for key, value in (payload.get("partitions") or {}).items()}
    overlay = {node["key"]: GraphNode.from_dict(node) for node in payload.get("overlay_nodes") or []}
    bridges = [GraphEdge.from_dict(item) for item in payload.get("edges") or []]
    diagnostics = [Diagnostic(**item) for item in payload.get("diagnostics") or []]
    return index_snapshot(
        version=payload["version"],
        generated_at=payload.get("generated_at") or "",
        partitions=partitions,
        overlay=overlay,
        bridges=bridges,
        diagnostics=diagnostics,
        fingerprints=list(payload.get("bridge_fingerprints") or []),
    )


def published_dict(snapshot: Snapshot) -> dict[str, Any]:
    partition_keys = {key for part in snapshot.partitions.values() for key in part.nodes}
    body = snapshot.to_dict()
    body["overlay_nodes"] = [node.to_dict() for key, node in snapshot.nodes.items() if key not in partition_keys]
    return body


def graph_slice(snapshot: Snapshot, *, service: str = "", kind: str = "", query: str = "", limit: int = 100, offset: int = 0) -> dict[str, Any]:
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))
    needle = query.lower()
    selected = []
    for node in snapshot.nodes.values():
        if service and node.service_id != service:
            continue
        if kind and node.kind != kind and node.category != kind:
            continue
        haystack = f"{node.name}\n{node.qualified_name}\n{node.file_path}".lower()
        if needle and needle not in haystack:
            continue
        selected.append(node)
    selected.sort(key=lambda node: (node.service_id, node.file_path, node.line_start or 0, node.qualified_name, node.key))
    page = selected[offset : offset + limit]
    keys = {node.key for node in page}
    incident = []
    for edge in snapshot.edges:
        if edge.source in keys or edge.target in keys:
            incident.append(edge.to_dict())
            if len(incident) >= limit * 4:
                break
    return {
        "version": snapshot.version,
        "total": len(selected),
        "limit": limit,
        "offset": offset,
        "truncated": offset + len(page) < len(selected),
        "nodes": [node.to_dict() for node in page],
        "edges": incident,
    }


def repo_for(manifest: ClusterManifest, service_id: str):
    return resolved_repo(manifest, service_id)


def http_bridges(partitions, model: ContractModel, overlay, bridges, diagnostics) -> None:
    route_nodes: dict[tuple[str, str, str, str], GraphNode] = {}
    for route in model.routes:
        node = _http_node(route)
        route_nodes[(route.service_id, route.method, route.path, route.version)] = node
        overlay[node.key] = node
        handler, quality = _bind_handler(partitions.get(route.service_id), route)
        if handler is None:
            handler = _handler_anchor(route)
            overlay[handler.key] = handler
            if route.handler_name or route.handler_kind == "inline":
                diagnostics.append(
                    Diagnostic(
                        code="handler_anchor",
                        message=(
                            f"{route.method} {route.path} is bound to an extracted handler anchor"
                            if route.handler_kind == "inline"
                            else f"{route.method} {route.path} handler {route.handler_name} did not match an exact CRG symbol"
                        ),
                        service_id=route.service_id,
                        file=route.file,
                        line=route.handler_line or route.line,
                    )
                )
        bridges.append(_bridge(node.key, handler.key, HANDLED_BY, route.source, route.file, route.line, node.metadata["identity"], quality))
    for call in model.calls:
        contract = route_nodes.get((call.provider_id, call.method, call.path, call.version))
        if contract is None:
            continue
        caller, quality = _bind_call(partitions.get(call.service_id), call)
        if caller is None:
            caller = _file_anchor(call.service_id, call.file, call.line)
            overlay[caller.key] = caller
            quality = "file"
        bridges.append(
            _bridge(caller.key, contract.key, CONSUMES_HTTP, "extracted", call.file, call.line, contract.metadata["identity"], quality, call.confidence)
        )


def grpc_bridges(partitions, manifest: ClusterManifest, overlay, bridges, diagnostics) -> None:
    for service in manifest.services:
        for contract in service.contracts.grpc:
            identity = f"grpc:{contract.version}:{contract.package}.{contract.service}/{contract.method}"
            node = _contract_node(service.id, identity, GRPC_CONTRACT, f"{contract.service}/{contract.method}", {"package": contract.package})
            overlay[node.key] = node
            if contract.implementation:
                impl, quality = resolve_symbol(partitions.get(service.id), contract.implementation)
                if impl is None:
                    diagnostics.append(Diagnostic(code="grpc_implementation_unresolved", message=f"{identity} implementation {contract.implementation} did not resolve", service_id=service.id, severity="error"))
                else:
                    bridges.append(_bridge(node.key, impl.key, IMPLEMENTED_BY, "declared", impl.file_path, impl.line_start or 0, identity, quality))
            for caller in contract.callers:
                target, quality = resolve_symbol(partitions.get(caller.service), caller.symbol)
                if target is None:
                    diagnostics.append(Diagnostic(code="grpc_caller_unresolved", message=f"{identity} caller {caller.service}:{caller.symbol} did not resolve", service_id=caller.service, severity="error"))
                    continue
                bridges.append(_bridge(target.key, node.key, CONSUMES_GRPC, "declared", target.file_path, target.line_start or 0, identity, quality))


def event_bridges(partitions, manifest: ClusterManifest, overlay, bridges, diagnostics) -> None:
    for service in manifest.services:
        for contract in service.contracts.events:
            identity = f"event:{contract.broker}:{contract.topic}@{contract.schema_version}"
            node = _contract_node(service.id, identity, EVENT_CONTRACT, contract.topic, {"broker": contract.broker, "schema_version": contract.schema_version})
            overlay[node.key] = node
            for producer in contract.producers:
                symbol, quality = resolve_symbol(partitions.get(service.id), producer)
                if symbol is None:
                    diagnostics.append(Diagnostic(code="event_producer_unresolved", message=f"{identity} producer {producer} did not resolve", service_id=service.id))
                    continue
                bridges.append(_bridge(symbol.key, node.key, PRODUCES_EVENT, "declared", symbol.file_path, symbol.line_start or 0, identity, quality))
            for consumer in contract.consumers:
                symbol, quality = resolve_symbol(partitions.get(consumer.service), consumer.symbol)
                if symbol is None:
                    diagnostics.append(Diagnostic(code="event_consumer_unresolved", message=f"{identity} consumer {consumer.service}:{consumer.symbol} did not resolve", service_id=consumer.service))
                    continue
                bridges.append(
                    _bridge(node.key, symbol.key, CONSUMED_BY, "declared", symbol.file_path, symbol.line_start or 0, identity, quality, extra={"consumer_group": consumer.group})
                )


def expected_bridges(partitions, manifest, overlay, bridges, diagnostics) -> None:
    for service in manifest.services:
        for relation in service.expected:
            if relation.kind != "http" or not relation.symbol:
                continue
            provider = relation.provider or service.id
            identity = f"http:{relation.version}:{relation.method.upper()}:{relation.path}"
            key = symbol_key(provider, "", identity, HTTP_CONTRACT)
            overlay.setdefault(
                key,
                GraphNode(
                    key=key,
                    category=HTTP_CONTRACT,
                    service_id=provider,
                    name=f"{relation.method.upper()} {relation.path}",
                    qualified_name=identity,
                    file_path="",
                    kind=HTTP_CONTRACT,
                    metadata={"identity": identity, "declared": True},
                ),
            )
            symbol, quality = resolve_symbol(partitions.get(relation.consumer), relation.symbol)
            if symbol is None:
                diagnostics.append(Diagnostic(code="expected_unresolved", message=f"Expected consumer {relation.consumer}:{relation.symbol} did not resolve", service_id=relation.consumer))
                continue
            bridges.append(_bridge(symbol.key, key, CONSUMES_HTTP, "declared", symbol.file_path, symbol.line_start or 0, identity, quality))


def annotate_bounds(partitions: dict[str, Partition], files_by_service: dict[str, dict[str, str]]) -> None:
    for part in partitions.values():
        files = files_by_service.get(part.service_id, {})
        hashed = {path: hashlib.sha256(text.encode("utf-8")).hexdigest() for path, text in files.items()}
        part.file_hashes = hashed
        mismatched: set[str] = set()
        for node in part.nodes.values():
            digest = hashed.get(node.file_path)
            recorded = str(node.metadata.get("file_hash") or "")
            if digest and recorded and recorded != digest:
                node.bounds_verified = False
                mismatched.add(node.file_path)
            elif digest and node.line_start and node.line_end and node.file_path in files:
                lines = files[node.file_path].splitlines()
                chunk = "\n".join(lines[node.line_start - 1 : node.line_end])
                node.span_hash = fingerprint_text(chunk)
        for path in sorted(mismatched):
            part.diagnostics.append(
                Diagnostic(
                    code="source_index_mismatch",
                    message=f"{path} differs from the indexed CRG file hash; those line bounds are not verified for the new source",
                    service_id=part.service_id,
                    file=path,
                )
            )
            if part.quality == "ok":
                part.quality = "stale"


def resolve_symbol(partition: Partition | None, reference: str) -> tuple[GraphNode | None, str]:
    if partition is None or not reference:
        return None, "unbound"
    file, name = ("", reference)
    if "::" in reference:
        file, name = reference.split("::", 1)
    found = named_symbols(partition, file, name)
    if len(found) == 1:
        return found[0], "exact"
    qualified = [node for node in partition.nodes.values() if node.qualified_name == reference]
    if len(qualified) == 1:
        return qualified[0], "exact"
    return None, "ambiguous" if found else "unbound"


def named_symbols(partition: Partition, file: str, name: str) -> list[GraphNode]:
    found = []
    for node in partition.nodes.values():
        if node.category != CODE_SYMBOL or node.kind == "File":
            continue
        if file and node.file_path != file:
            continue
        if node.name == name or node.qualified_name == name or node.qualified_name.endswith(f"::{name}"):
            found.append(node)
    return found


def _http_node(route: HttpRoute) -> GraphNode:
    identity = f"http:{route.version}:{route.method}:{route.path}"
    return GraphNode(
        key=symbol_key(route.service_id, route.file, identity, HTTP_CONTRACT),
        category=HTTP_CONTRACT,
        service_id=route.service_id,
        name=f"{route.method} {route.path}",
        qualified_name=identity,
        file_path=route.file,
        line_start=route.line or None,
        kind=HTTP_CONTRACT,
        metadata={"identity": identity, "source": route.source, "version": route.version},
    )


def _contract_node(service_id: str, identity: str, category: str, name: str, extra: dict) -> GraphNode:
    return GraphNode(
        key=symbol_key(service_id, "", identity, category),
        category=category,
        service_id=service_id,
        name=name,
        qualified_name=identity,
        file_path="",
        kind=category,
        metadata={"identity": identity, "declared": True, **extra},
    )


def _handler_anchor(route: HttpRoute) -> GraphNode:
    lexical = f"handler:{route.method}:{route.path}:{route.file}:{route.handler_line or route.line}"
    return GraphNode(
        key=symbol_key(route.service_id, route.file, lexical, HANDLER_ANCHOR),
        category=HANDLER_ANCHOR,
        service_id=route.service_id,
        name=route.handler_name or f"<inline {route.method} {route.path}>",
        qualified_name=lexical,
        file_path=route.file,
        line_start=route.handler_line or route.line or None,
        kind=HANDLER_ANCHOR,
        bounds_verified=True,
        metadata={"resolution": "handler_anchor", "exact_crg_symbol": False},
    )


def _file_anchor(service_id: str, file: str, line: int) -> GraphNode:
    lexical = f"file:{file}"
    return GraphNode(
        key=symbol_key(service_id, file, lexical, FILE_ANCHOR),
        category=FILE_ANCHOR,
        service_id=service_id,
        name=file,
        qualified_name=lexical,
        file_path=file,
        line_start=line or None,
        kind="File",
        bounds_verified=False,
        metadata={"resolution": "file", "exact_crg_symbol": False},
    )


def _bind_handler(partition: Partition | None, route: HttpRoute) -> tuple[GraphNode | None, str]:
    if partition is None:
        return None, "unbound"
    if route.handler_kind in {"named", "mapped", "declared"} and route.handler_name:
        found = named_symbols(partition, route.file, route.handler_name.split("::")[-1])
        if route.handler_kind != "named":
            found = named_symbols(partition, "", route.handler_name.split("::")[-1]) or found
        if len(found) == 1:
            return found[0], "exact"
        return None, "ambiguous" if len(found) > 1 else "unbound"
    if route.handler_kind == "inline":
        exact = [
            node
            for node in partition.nodes.values()
            if node.category == CODE_SYMBOL and node.kind != "File" and node.file_path == route.file and node.line_start == route.handler_line
        ]
        if len(exact) == 1:
            return exact[0], "exact"
    return None, "anchor"


def _bind_call(partition: Partition | None, call: HttpCall) -> tuple[GraphNode | None, str]:
    if partition is None:
        return None, "unbound"
    containing = [
        node
        for node in partition.nodes.values()
        if node.category == CODE_SYMBOL
        and node.kind != "File"
        and node.file_path == call.file
        and node.line_start is not None
        and node.line_end is not None
        and node.line_start <= call.line <= node.line_end
    ]
    if not containing:
        return None, "file"
    containing.sort(key=lambda node: ((node.line_end or 0) - (node.line_start or 0), node.qualified_name))
    return containing[0], "exact"


def _bridge(source, target, relation, status, file, line, contract_id, quality, confidence: float | None = 1.0, extra=None) -> GraphEdge:
    evidence = {
        "source": "manifest" if status == "declared" else "extractor",
        "status": "declared" if status == "declared" else "extracted",
        "file": file,
        "line": line,
        "confidence": confidence,
        "confidence_tier": "DECLARED" if status == "declared" else "EXTRACTED",
        "contract_id": contract_id,
        "extractor": "nami-contract-1",
        "resolution": quality,
        "injected": True,
        "exact_symbol": quality == "exact",
    }
    if extra:
        evidence.update(extra)
    return GraphEdge(source=source, target=target, relation=relation, evidence=evidence)


def _diagnostics(model: ContractModel) -> list[Diagnostic]:
    return [
        Diagnostic(code=entry.get("code", "contract"), message=entry.get("message", ""), service_id=entry.get("service_id", ""))
        for entry in [*model.diagnostics, *model.ambiguous]
    ]
