"""Discover topology from local checkouts. It does not clone or call GitHub."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from dep_intel.cluster_manifest import select_manifest
from dep_intel.config import Settings
from dep_intel.sources import RepoRef, load_repos
from dep_intel.store import now, write_json
from dep_intel.topology.merge import effective
from dep_intel.topology.models import SCHEMA, SCHEMA_VERSION
from dep_intel.topology.sources.docker_compose import service_aliases as compose_aliases
from dep_intel.topology.sources.http import scan as scan_http
from dep_intel.topology.sources.kubernetes import service_aliases as kube_aliases
from dep_intel.topology.sources.openapi import operations as openapi_operations
from dep_intel.topology.sources.package_metadata import aliases_from_packages


def discover(settings: Settings) -> dict[str, Any]:
    repos = load_repos(settings)
    services = []
    aliases: list[dict] = []
    calls: list[dict] = []
    operations: list[dict] = []
    notes: list[str] = []
    unavailable = []
    for ref in repos:
        root = _local_root(ref, settings)
        if root is None:
            unavailable.append(ref.name)
            services.append({"service": ref.name, "repository": ref.raw, "checkout": "not_scanned"})
            continue
        service = ref.name
        services.append({"service": service, "repository": ref.raw, "checkout": "available"})
        aliases.extend(aliases_from_packages(root, service))
        aliases.extend(kube_aliases(root, service))
        aliases.extend(compose_aliases(root, service))
        http = scan_http(root)
        operations.extend({**item, "service": service} for item in openapi_operations(root))
        operations.extend({**item, "service": service} for item in http["routes"])
        for call in http["calls"]:
            calls.append({**call, "service": service})
        notes.extend(f"{service}: {item}" for item in http.get("notes") or [])
    bindings = _bindings(settings, repos)
    discovered = {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "generated_at": now(),
        "services": services,
        "aliases": aliases,
        "calls": calls,
        "operations": operations[:120],
        "source": "deterministic",
        "not_scanned": unavailable,
        "notes": notes[:40],
        "note": "Remote allowlist entries are listed without a network fetch. Relationship discovery uses an existing local checkout.",
    }
    result = effective(discovered, settings.root, bindings)
    result["generated_at"] = discovered["generated_at"]
    result["notes"] = notes[:40]
    result["not_scanned"] = unavailable
    directory = settings.output_dir / "topology"
    directory.mkdir(parents=True, exist_ok=True)
    write_json(directory, "discovered.json", discovered)
    write_json(directory, "effective.json", result)
    return result


def load_effective(settings: Settings) -> dict[str, Any] | None:
    path = settings.output_dir / "topology" / "effective.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _local_root(ref: RepoRef, settings: Settings) -> Path | None:
    if ref.path and ref.path.is_dir():
        return ref.path
    checkout = settings.output_dir / "checkouts" / ref.slug
    if (checkout / ".git").exists():
        return checkout
    return None


def _bindings(settings: Settings, repos: list) -> list[dict]:
    manifest, _errors, notices = select_manifest(settings.root, repos, settings.cluster_manifest)
    if manifest is None or notices:
        return []
    rows = []
    names = {service.id: service.repository for service in manifest.services}
    for service in manifest.services:
        source = Path(service.repository).name
        for item in service.client_bindings:
            rows.append({
                "service": source,
                "file": item.file,
                "client": item.client,
                "target": names.get(item.target_service, item.target_service),
            })
            target = names.get(item.target_service, item.target_service)
            rows[-1]["target"] = Path(str(target)).name if "/" in str(target) else target
    return rows
