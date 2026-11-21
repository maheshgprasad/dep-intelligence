"""Write cluster-manifest.json from repos.txt.

repos.txt is the source of truth for repository identity and declared cluster
membership. The manifest is a generated runtime file. An existing manifest's
contract bindings, client bindings, and host aliases are copied onto the
matching service so generation does not drop topology compatibility data.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import yaml

from dep_intel.cluster_manifest import (
    ClusterDecl,
    ClusterManifest,
    GeneratedManifestMetadata,
    ImpactSettings,
    ServiceDecl,
    WatchSettings,
    WorkerSettings,
)
from dep_intel.config import Settings
from dep_intel.sources import RepoRef, load_repos


def destination(settings: Settings) -> Path:
    chosen = settings.cluster_manifest
    if chosen is None:
        override = os.environ.get("CLUSTER_MANIFEST", "")
        chosen = Path(override) if override else None
    if chosen is None:
        return settings.root / "cluster-manifest.json"
    return chosen if chosen.is_absolute() else settings.root / chosen


def source_hash(repos: list[RepoRef]) -> str:
    rows = sorted((repo.cluster, repo.service_id, repo.kind, repo.raw) for repo in repos)
    digest = hashlib.sha256(json.dumps(rows, separators=(",", ":")).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def build_manifest(repos: list[RepoRef], previous: ClusterManifest | None, digest: str) -> ClusterManifest:
    prior = {service.id: service for service in previous.services} if previous is not None else {}
    by_repository = {service.repository: service for service in previous.services} if previous is not None else {}
    services: list[ServiceDecl] = []
    grouped: dict[str, list[str]] = {}
    for repo in repos:
        existing = prior.get(repo.service_id) or by_repository.get(repo.raw)
        if existing is None:
            services.append(ServiceDecl(id=repo.service_id, repository=repo.raw))
        else:
            payload = existing.model_dump()
            payload["id"] = repo.service_id
            payload["repository"] = repo.raw
            services.append(ServiceDecl.model_validate(payload))
        if repo.cluster:
            grouped.setdefault(repo.cluster, []).append(repo.service_id)
    services.sort(key=lambda service: service.id)
    clusters = [
        ClusterDecl(id=cluster_id, services=sorted(set(grouped[cluster_id])))
        for cluster_id in sorted(grouped)
    ]
    return ClusterManifest(
        schema_version=2,
        generated=GeneratedManifestMetadata(generated_by="nami-trace", source="repos.txt", source_hash=digest),
        clusters=clusters,
        services=services,
        impact=previous.impact if previous is not None else ImpactSettings(),
        watch=previous.watch if previous is not None else WatchSettings(),
        workers=previous.workers if previous is not None else WorkerSettings(),
    )


def manifest_bytes(manifest: ClusterManifest) -> bytes:
    payload = manifest.model_dump(mode="json")
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")


def ensure_manifest(settings: Settings) -> Path | None:
    """Write the schema-v2 manifest when repos.txt changed. Identical bytes are left untouched."""
    if not settings.repos_file.is_file():
        return None
    repos = load_repos(settings)
    path = destination(settings)
    previous = _read_previous(path)
    digest = source_hash(repos)
    if previous is not None and previous.generated is not None and previous.generated.source_hash == digest and path.is_file():
        return path
    manifest = build_manifest(repos, previous, digest)
    payload = manifest_bytes(manifest)
    if path.is_file() and path.read_bytes() == payload:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)
    _sync_topology_aliases(settings.root, manifest)
    return path


def _read_previous(path: Path) -> ClusterManifest | None:
    if not path.is_file():
        return None
    try:
        return ClusterManifest.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def _sync_topology_aliases(root: Path, manifest: ClusterManifest) -> None:
    """Copy manifest host aliases into nami-topology.yaml without removing the manifest copies."""
    aliases: dict[str, str] = {}
    for service in manifest.services:
        for alias in service.http.host_aliases:
            aliases.setdefault(alias, service.id)
    if not aliases:
        return
    path = root / "nami-topology.yaml"
    document: dict = {}
    if path.is_file():
        try:
            loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            loaded = {}
        if isinstance(loaded, dict):
            document = loaded
    current = document.get("aliases") if isinstance(document.get("aliases"), dict) else {}
    merged = dict(current)
    for alias, service_id in aliases.items():
        merged.setdefault(alias, {"service": service_id})
    if merged == current and path.is_file():
        return
    document["aliases"] = dict(sorted(merged.items()))
    text = yaml.safe_dump(document, sort_keys=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)
