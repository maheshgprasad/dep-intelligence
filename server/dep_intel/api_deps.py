"""REST client/server detection across the repositories in one scan."""

from __future__ import annotations

import re

from dep_intel.config import Settings
from dep_intel.sources import Workspace
from dep_intel.store import now, write_json

_ROUTE = re.compile(
    r"\.(get|post|put|patch|delete)\(\s*['\"]([^'\"]+)['\"]",
    re.IGNORECASE,
)
_AXIOS = re.compile(
    r"axios\.(get|post|put|patch|delete)\(\s*['\"]([^'\"]+)['\"]",
    re.IGNORECASE,
)
_FETCH = re.compile(r"fetch\(\s*['\"]([^'\"]+)['\"]")
_GO_HANDLE = re.compile(r'HandleFunc\(\s*"([^"]+)"')
_GO_CLIENT = re.compile(r'http\.(Get|Post|Put|Delete)\(\s*"([^"]+)"')


def detect_api_dependencies(workspaces: list[Workspace], settings: Settings) -> dict:
    services: dict[str, dict] = {}
    exposes: list[tuple[str, str, str]] = []
    consumes: list[tuple[str, str, str, str]] = []
    for workspace in workspaces:
        provided = []
        exposed = []
        consumed = []
        for path, text in workspace.files.items():
            filename = path.rsplit("/", 1)[-1]
            if filename.lower() in {"openapi.yaml", "openapi.yml", "swagger.json", "swagger.yaml"}:
                provided.append({"api": filename, "path": path})
            for method, route in _ROUTE.findall(text):
                exposed.append({"path": route, "method": method.upper(), "framework": "express", "file": path})
                exposes.append((workspace.ref.name, method.upper(), route))
            for method, url in _AXIOS.findall(text):
                consumed.append({"url": url, "method": method.upper(), "type": "axios", "file": path})
                consumes.append((workspace.ref.name, method.upper(), url, "axios"))
            for url in _FETCH.findall(text):
                consumed.append({"url": url, "method": "GET", "type": "fetch", "file": path})
                consumes.append((workspace.ref.name, "GET", url, "fetch"))
            for route in _GO_HANDLE.findall(text):
                exposed.append({"path": route, "method": "GET", "framework": "net/http", "file": path})
                exposes.append((workspace.ref.name, "GET", route))
            for method, url in _GO_CLIENT.findall(text):
                consumed.append({"url": url, "method": method.upper(), "type": "net/http", "file": path})
                consumes.append((workspace.ref.name, method.upper(), url, "net/http"))
        services[workspace.ref.name] = {"provides": provided, "consumes": consumed, "exposes": exposed}

    dependencies = []
    for source, method, url, _kind in consumes:
        target_path = url.split("?", 1)[0]
        if "://" in target_path:
            target_path = "/" + target_path.split("/", 3)[-1]
        for target, exposed_method, exposed_path in exposes:
            if target == source:
                continue
            if exposed_path == target_path and exposed_method == method:
                dependencies.append(
                    {
                        "from": source,
                        "to": target,
                        "type": "api-call",
                        "endpoint": exposed_path,
                        "method": method,
                        "confidence": "high",
                    }
                )
    payload = {
        "meta": {"generated_at": now(), "source": "dep-intel", "repos_file": str(settings.repos_file)},
        "api_dependencies": {"services": services, "dependencies": dependencies},
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
