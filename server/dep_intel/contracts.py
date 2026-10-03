"""Contract normalization and provider resolution.

Provider identity is resolved before route templates are matched. Relative
client URLs stay unresolved unless a client binding or a static base URL
identifies one service.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath
from urllib.parse import urlparse

from dep_intel.cluster_manifest import ClusterManifest, ServiceDecl
from dep_intel.contract_extractors.javascript import (
    CallHit,
    FileExtract,
    ResolvedRoute,
    extract_javascript,
    resolve_routes,
)
from dep_intel.contract_extractors.openapi import OpenAPIDocument, Operation, parse_openapi
from dep_intel.graph_models import EXTRACTOR_VERSION

_CONTRACT_SUFFIXES = {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".json", ".yml", ".yaml"}
_OPENAPI_NAMES = {"openapi.yaml", "openapi.yml", "openapi.json", "swagger.json", "swagger.yaml", "swagger.yml"}


@dataclass
class HttpRoute:
    service_id: str
    method: str
    path: str
    version: str
    file: str
    line: int
    handler_name: str
    handler_kind: str
    handler_line: int
    source: str


@dataclass
class HttpCall:
    service_id: str
    method: str
    path: str
    version: str
    provider_id: str
    file: str
    line: int
    client: str
    confidence: float | None
    status: str
    dynamic: bool = False


@dataclass
class ContractModel:
    routes: list[HttpRoute] = field(default_factory=list)
    calls: list[HttpCall] = field(default_factory=list)
    operations: list[tuple[str, Operation]] = field(default_factory=list)
    diagnostics: list[dict[str, str]] = field(default_factory=list)
    ambiguous: list[dict[str, str]] = field(default_factory=list)
    extractor: str = EXTRACTOR_VERSION


def normalize_template(path: str) -> str:
    path = path.split("?", 1)[0].split("#", 1)[0]
    if not path.startswith("/"):
        path = "/" + path
    collapsed = []
    for part in path.split("/"):
        if part == "" and collapsed:
            continue
        if part.startswith(":") and len(part) > 1:
            part = "{" + part[1:] + "}"
        collapsed.append(part)
    normalized = "/".join(collapsed)
    if path.endswith("/") and not normalized.endswith("/"):
        normalized += "/"
    return normalized or "/"


def join_base(base: str, path: str) -> str:
    if not base:
        return normalize_template(path)
    return normalize_template(base.rstrip("/") + "/" + path.lstrip("/"))


def match_template(concrete: str, template: str) -> tuple[bool, int]:
    left = _segments(normalize_template(concrete))
    right = _segments(normalize_template(template))
    if len(left) != len(right):
        return False, 0
    static = 0
    for actual, pattern in zip(left, right, strict=True):
        if pattern.startswith("{") and pattern.endswith("}") and len(pattern) > 2:
            if actual == "":
                return False, 0
            continue
        if actual != pattern:
            return False, 0
        static += 1
    return True, static


def build_contract_model(manifest: ClusterManifest, files_by_service: dict[str, dict[str, str]]) -> ContractModel:
    model = ContractModel()
    extracted: dict[str, dict[str, FileExtract]] = {}
    for service in manifest.services:
        files = {
            path: text
            for path, text in files_by_service.get(service.id, {}).items()
            if PurePosixPath(path).suffix.lower() in {".js", ".jsx", ".mjs", ".cjs"}
        }
        parsed = {path: extract_javascript(path, text) for path, text in sorted(files.items())}
        extracted[service.id] = parsed
        routes, diagnostics = resolve_routes(parsed)
        for note in diagnostics:
            model.diagnostics.append({"code": "router_resolution", "message": note, "service_id": service.id})
        for route in routes:
            model.routes.append(
                HttpRoute(
                    service_id=service.id,
                    method=route.method,
                    path=join_base(service.http.base_path, route.path),
                    version="",
                    file=route.file,
                    line=route.line,
                    handler_name=route.handler_name,
                    handler_kind=route.handler_kind,
                    handler_line=route.handler_line,
                    source="extracted",
                )
            )
        for document in _openapi_documents(service, files_by_service.get(service.id, {})):
            for operation in document.operations:
                model.operations.append((service.id, operation))
                if operation.handler:
                    model.routes.append(
                        HttpRoute(
                            service_id=service.id,
                            method=operation.method,
                            path=join_base(service.http.base_path, operation.path),
                            version=operation.version,
                            file=operation.file,
                            line=1,
                            handler_name=operation.handler,
                            handler_kind="mapped",
                            handler_line=1,
                            source="openapi",
                        )
                    )
            for note in document.diagnostics:
                model.diagnostics.append({"code": "openapi", "message": note, "service_id": service.id})
        _declared_http(service, model)
    hosts = _host_index(manifest)
    route_index: dict[tuple[str, str], list[HttpRoute]] = {}
    for route in model.routes:
        route_index.setdefault((route.service_id, route.method), []).append(route)
    for service in manifest.services:
        for parsed in extracted.get(service.id, {}).values():
            for call in parsed.calls:
                _resolve_call(manifest, service, call, hosts, route_index, model)
    return model


def _declared_http(service: ServiceDecl, model: ContractModel) -> None:
    for contract in service.contracts.http:
        model.routes.append(
            HttpRoute(
                service_id=service.id,
                method=contract.method.upper(),
                path=join_base(service.http.base_path, contract.path),
                version=contract.version,
                file="",
                line=0,
                handler_name=contract.handler,
                handler_kind="declared" if contract.handler else "unbound",
                handler_line=0,
                source="declared",
            )
        )


def _resolve_call(manifest, service: ServiceDecl, call: CallHit, hosts, route_index, model: ContractModel) -> None:
    if call.url_dynamic or call.base_dynamic or not call.method:
        model.diagnostics.append(
            {
                "code": "dynamic_client_url",
                "message": f"{call.file}:{call.line} {call.client} {call.method or 'method'} URL is not statically resolved",
                "service_id": service.id,
            }
        )
        return
    provider, reason = _provider_for(service, call, hosts, manifest)
    if provider is None:
        model.diagnostics.append(
            {
                "code": reason,
                "message": f"{call.file}:{call.line} {call.method} {call.url or call.base_url} is {reason.replace('_', ' ')}",
                "service_id": service.id,
            }
        )
        return
    path, version = _call_path(call)
    candidates = _candidates(route_index.get((provider, call.method), []), path, version)
    if not candidates:
        model.diagnostics.append(
            {
                "code": "route_unmatched",
                "message": f"{call.file}:{call.line} {call.method} {path} did not match a route on {provider}",
                "service_id": service.id,
            }
        )
        return
    if len(candidates) > 1:
        model.ambiguous.append(
            {
                "code": "ambiguous_route",
                "message": f"{call.file}:{call.line} {call.method} {path} matches {len(candidates)} routes on {provider}",
                "service_id": service.id,
            }
        )
        return
    route = candidates[0]
    model.calls.append(
        HttpCall(
            service_id=service.id,
            method=route.method,
            path=route.path,
            version=route.version,
            provider_id=provider,
            file=call.file,
            line=call.line,
            client=call.client,
            confidence=1.0 if route.source != "declared" else None,
            status="extracted",
        )
    )


def _provider_for(service: ServiceDecl, call: CallHit, hosts: dict[str, list[str]], manifest: ClusterManifest) -> tuple[str | None, str]:
    absolute = _absolute_url(call)
    hostname = ""
    if absolute:
        hostname = (urlparse(absolute).hostname or "").lower()
    bindings = [
        item
        for item in service.client_bindings
        if item.file == call.file and _client_matches(item.client, call)
    ]
    if hostname:
        owners = hosts.get(hostname, [])
        if len(owners) == 1:
            return owners[0], "hostname"
        if len(owners) > 1:
            return None, "ambiguous_provider"
        if len(bindings) == 1 and not absolute:
            return bindings[0].target_service, "client_binding"
        return None, "unresolved_provider"
    if len(bindings) == 1:
        return bindings[0].target_service, "client_binding"
    if len(bindings) > 1:
        targets = {item.target_service for item in bindings}
        if len(targets) == 1:
            return next(iter(targets)), "client_binding"
        return None, "ambiguous_provider"
    if call.base_url:
        base_host = (urlparse(call.base_url).hostname or "").lower()
        owners = hosts.get(base_host, [])
        if len(owners) == 1:
            return owners[0], "base_url"
        if len(owners) > 1:
            return None, "ambiguous_provider"
    return None, "unresolved_provider"


def _client_matches(declared: str, call: CallHit) -> bool:
    if declared == call.client:
        return True
    if declared == "axios" and call.client_kind in {"axios_module", "axios_instance"} and call.client in {"axios", declared}:
        return call.client == "axios" or declared == call.client
    if declared == "fetch" and call.client_kind == "fetch":
        return True
    return False


def _absolute_url(call: CallHit) -> str:
    if call.url.startswith("http://") or call.url.startswith("https://"):
        return call.url
    if call.base_url and call.url.startswith("/"):
        return call.base_url.rstrip("/") + call.url
    if call.base_url.startswith("http://") or call.base_url.startswith("https://"):
        return call.base_url.rstrip("/") + "/" + call.url.lstrip("/")
    return ""


def _call_path(call: CallHit) -> tuple[str, str]:
    absolute = _absolute_url(call)
    if absolute:
        parsed = urlparse(absolute)
        return normalize_template(parsed.path or "/"), ""
    return normalize_template(call.url), ""


def _candidates(routes: list[HttpRoute], path: str, version: str) -> list[HttpRoute]:
    scored: list[tuple[int, HttpRoute]] = []
    for route in routes:
        if version and route.version and route.version != version:
            continue
        matched, static = match_template(path, route.path)
        if matched:
            scored.append((static, route))
    if not scored:
        return []
    best = max(item[0] for item in scored)
    top = [route for static, route in scored if static == best]
    # Identical declarations are one route. Different handlers at the same specificity stay ambiguous.
    unique: list[HttpRoute] = []
    for route in top:
        if any(other.path == route.path and other.handler_line == route.handler_line and other.file == route.file for other in unique):
            continue
        unique.append(route)
    return unique


def _host_index(manifest: ClusterManifest) -> dict[str, list[str]]:
    hosts: dict[str, list[str]] = {}
    for service in manifest.services:
        names = set(service.http.host_aliases)
        for origin in service.http.origins:
            host = (urlparse(origin).hostname or "").lower()
            if host:
                names.add(host)
        for name in names:
            hosts.setdefault(name.lower(), []).append(service.id)
    return hosts


def _openapi_documents(service: ServiceDecl, files: dict[str, str]) -> list[OpenAPIDocument]:
    selected: list[str] = []
    for item in service.contracts.openapi:
        name = item.get("file", "")
        if name:
            selected.append(name)
    if not selected:
        selected = [path for path in files if PurePosixPath(path).name.lower() in _OPENAPI_NAMES]
    documents = []
    for path in selected:
        text = files.get(path)
        if text is None:
            documents.append(OpenAPIDocument(file=path, diagnostics=[f"{path}: OpenAPI file was not in the repository scan"]))
            continue

        def loader(ref: str, base=path) -> str | None:
            target = str(PurePosixPath(base).parent / ref)
            return files.get(target)

        documents.append(parse_openapi(text, path, loader))
    return documents


def _segments(path: str) -> list[str]:
    if path == "/":
        return [""]
    parts = path.split("/")
    if path.endswith("/"):
        return parts[1:]
    return [part for part in parts[1:]]
