"""REST client/server detection across the repositories in one scan.

JavaScript routes and clients come from the tree-sitter extractor. A relative
client URL is not linked to every matching route; provider identity has to
resolve through the cluster manifest.
"""

from __future__ import annotations

import re

from dep_intel.cluster_manifest import applicable_manifest, manifest_path, suggestion_for_missing_manifest
from dep_intel.config import Settings
from dep_intel.contract_extractors.javascript import extract_javascript, resolve_routes
from dep_intel.contract_extractors.openapi import parse_openapi
from dep_intel.contracts import build_contract_model
from dep_intel.sources import Workspace, read_contract_sources
from dep_intel.store import now, write_json

_GO_HANDLE = re.compile(r'HandleFunc\(\s*"([^"]+)"')
_GO_CLIENT = re.compile(r'http\.(Get|Post|Put|Delete)\(\s*"([^"]+)"')
_PY_DECORATOR = re.compile(
    r"@(?:\w+)\.(get|post|put|patch|delete|options|head)\(\s*['\"]([^'\"]+)['\"]",
    re.IGNORECASE,
)
_PY_ROUTE = re.compile(
    r"@(?:\w+)\.route\(\s*['\"]([^'\"]+)['\"](?:[^)\n]*methods\s*=\s*\[([^\]]*)\])?",
    re.IGNORECASE,
)
_OPENAPI_NAMES = {"openapi.yaml", "openapi.yml", "openapi.json", "swagger.json", "swagger.yaml", "swagger.yml"}


def detect_api_dependencies(workspaces: list[Workspace], settings: Settings) -> dict:
    services: dict[str, dict] = {}
    diagnostics: list[dict] = []
    files_by_name: dict[str, dict[str, str]] = {}
    for workspace in workspaces:
        texts = _contract_texts(workspace)
        files_by_name[workspace.ref.name] = texts
        provided, exposed, consumed = _scan_workspace(workspace, texts, diagnostics)
        services[workspace.ref.name] = {"provides": provided, "consumes": consumed, "exposes": exposed}
        if workspace.note:
            diagnostics.append({"code": "checkout", "message": workspace.note, "service": workspace.ref.name})
        if workspace.truncated:
            diagnostics.append(
                {
                    "code": "source_scan_truncated",
                    "message": f"{workspace.ref.name} source sample was truncated before the contract pass",
                    "service": workspace.ref.name,
                }
            )

    dependencies = []
    manifest, errors, _notices = applicable_manifest(manifest_path(settings.root), [workspace.ref for workspace in workspaces], settings.root)
    if errors:
        diagnostics.extend({"code": "manifest", "message": error} for error in errors)
    elif manifest is None:
        diagnostics.append(suggestion_for_missing_manifest())
    else:
        by_service = {}
        for service in manifest.services:
            matched = next((workspace for workspace in workspaces if workspace.ref.raw == service.repository or workspace.ref.name == service.repository or (workspace.ref.path and service.repository.endswith(workspace.ref.path.name))), None)
            if matched is None:
                continue
            by_service[service.id] = files_by_name.get(matched.ref.name, {})
        model = build_contract_model(manifest, by_service)
        names = {service.id: service.repository for service in manifest.services}
        for call in model.calls:
            dependencies.append(
                {
                    "from": names.get(call.service_id, call.service_id),
                    "to": names.get(call.provider_id, call.provider_id),
                    "type": "api-call",
                    "endpoint": call.path,
                    "method": call.method,
                    "confidence": "high" if call.confidence == 1.0 else "declared",
                    "evidence": "manifest-binding" if call.status == "extracted" else call.status,
                }
            )
        diagnostics.extend(model.diagnostics)
        diagnostics.extend(model.ambiguous)

    payload = {
        "meta": {"generated_at": now(), "source": "dep-intel", "repos_file": str(settings.repos_file)},
        "api_dependencies": {"services": services, "dependencies": dependencies, "diagnostics": diagnostics},
        "summary": {
            "total_services": len(services),
            "total_api_specs": sum(len(item["provides"]) for item in services.values()),
            "total_dependencies": len(dependencies),
            "services_with_clients": sum(1 for item in services.values() if item["consumes"]),
        },
    }
    write_json(settings.output_dir, "api_dependencies.json", payload)
    return {
        "success": True,
        "message": f"Found {len(dependencies)} API dependencies across {len(services)} services.",
        "summary": payload["summary"],
    }


def _contract_texts(workspace: Workspace) -> dict[str, str]:
    if workspace.ref.path and workspace.ref.path.is_dir():
        files, _truncated = read_contract_sources(workspace.ref.path)
        return files
    return workspace.files


def _scan_workspace(workspace: Workspace, texts: dict[str, str], diagnostics: list[dict]) -> tuple[list, list, list]:
    provided, exposed, consumed = [], [], []
    javascript = {path: text for path, text in texts.items() if path.endswith((".js", ".jsx", ".mjs", ".cjs"))}
    parsed = {path: extract_javascript(path, text) for path, text in javascript.items()}
    routes, notes = resolve_routes(parsed)
    for note in notes:
        diagnostics.append({"code": "router_resolution", "message": note, "service": workspace.ref.name})
    for route in routes:
        if route.dynamic_path:
            continue
        exposed.append({"path": route.path, "method": route.method, "framework": "express", "file": route.file})
    for extracted in parsed.values():
        for call in extracted.calls:
            if call.url_dynamic or not call.method:
                diagnostics.append(
                    {
                        "code": "dynamic_client_url",
                        "message": f"{call.file}:{call.line} client URL is not statically resolved",
                        "service": workspace.ref.name,
                    }
                )
                continue
            consumed.append({"url": call.url, "method": call.method, "type": call.client_kind, "file": call.file})
    for path, text in texts.items():
        filename = path.rsplit("/", 1)[-1]
        if filename.lower() in _OPENAPI_NAMES or filename.lower().endswith((".yaml", ".yml", ".json")) and "openapi" in filename.lower():
            document = parse_openapi(text, path)
            provided.append({"api": filename, "path": path, "operations": len(document.operations)})
            for operation in document.operations:
                exposed.append({"path": operation.path, "method": operation.method, "framework": "openapi", "file": path, "handler": operation.handler})
        if path.endswith(".py") and not _is_test_path(path):
            for method, route in _PY_DECORATOR.findall(text):
                exposed.append({"path": route, "method": method.upper(), "framework": "python", "file": path})
            for route, methods in _PY_ROUTE.findall(text):
                found = re.findall(r"['\"](GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD)['\"]", methods or "", re.IGNORECASE)
                for method in found or ["GET"]:
                    exposed.append({"path": route, "method": method.upper(), "framework": "python", "file": path})
        if path.endswith(".go"):
            for route in _GO_HANDLE.findall(text):
                exposed.append({"path": route, "method": "GET", "framework": "net/http", "file": path})
            for method, url in _GO_CLIENT.findall(text):
                consumed.append({"url": url, "method": method.upper(), "type": "net/http", "file": path})
    return provided, exposed, consumed


def _is_test_path(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    if name.startswith("test_") or name.endswith("_test.py"):
        return True
    return any(part in {"tests", "test"} for part in path.split("/"))
