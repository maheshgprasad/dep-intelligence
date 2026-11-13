"""Write cluster-manifest.json from repos.txt.

The manifest is a generated runtime file. Repository identity and cluster
membership are not read back into repos.txt.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from dep_intel.cluster_manifest import (
    GENERATED_SCHEMA,
    ImpactSettings,
    WatchSettings,
    WorkerSettings,
    manifest_is_generated,
)
from dep_intel.config import Settings
from dep_intel.repo_config import RepoConfigError, RepositoryConfig, load_repo_config

USER_MANAGED_NOTICE = (
    "Existing user-managed cluster-manifest.json detected.\n"
    "\n"
    "Nami will not overwrite it automatically.\n"
    "\n"
    "Run:\n"
    "python scripts/nami-manifest.py generate --replace-existing\n"
    "\n"
    "after migrating required bindings to nami-topology.yaml."
)


@dataclass
class PrepareResult:
    config: RepositoryConfig | None
    errors: list[str] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)
    wrote: bool = False
    path: Path | None = None
    unchanged: bool = False


def configuration_hash(config: RepositoryConfig) -> str:
    payload = {
        "clusters": [{"id": cluster.id, "services": list(cluster.service_ids)} for cluster in config.clusters],
        "explicit": config.explicit,
        "repositories": [
            {
                "cluster_id": item.cluster_id,
                "repository": item.repo.raw,
                "service_id": item.service_id,
                "standalone": item.standalone,
            }
            for item in config.repositories
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def manifest_document(config: RepositoryConfig) -> dict:
    return {
        "schema_version": GENERATED_SCHEMA,
        "generated": {
            "generated_by": "nami-trace",
            "source": "repos.txt",
            "source_hash": configuration_hash(config),
            "membership": "declared" if config.explicit else "inferred",
        },
        "clusters": [{"id": cluster.id, "services": list(cluster.service_ids)} for cluster in config.clusters],
        "services": [
            {
                "id": item.service_id,
                "repository": item.repo.raw,
                "graph_db": ".code-review-graph/graph.db",
            }
            for item in config.repositories
        ],
        "impact": ImpactSettings().model_dump(),
        "watch": WatchSettings().model_dump(),
        "workers": WorkerSettings().model_dump(),
    }


def render_manifest(config: RepositoryConfig) -> str:
    return json.dumps(manifest_document(config), indent=2) + "\n"


def managed_manifest_path(settings: Settings) -> Path | None:
    """Project manifest written during startup.

    An explicit ``CLUSTER_MANIFEST`` is left untouched. A repos file that is
    not the project ``repos.txt`` does not rewrite the project manifest.
    """
    if settings.cluster_manifest is not None:
        return None
    if settings.repos_file.resolve() != (settings.root / "repos.txt").resolve():
        return None
    return settings.root / "cluster-manifest.json"


def prepare_repository_configuration(
    settings: Settings,
    *,
    target: Path | None = None,
    replace_existing: bool = False,
) -> PrepareResult:
    destination = target if target is not None else managed_manifest_path(settings)
    try:
        config = load_repo_config(settings)
    except RepoConfigError as exc:
        return PrepareResult(config=None, errors=list(exc.errors), path=destination)
    if destination is None or not settings.repos_file.is_file():
        return PrepareResult(config=config, path=destination)
    return _write_if_needed(config, destination, replace_existing=replace_existing)


def generation_summary(config: RepositoryConfig) -> dict[str, int]:
    clustered = sum(len(cluster.service_ids) for cluster in config.clusters)
    if config.explicit:
        standalone = sum(1 for item in config.repositories if item.standalone or item.cluster_id is None)
    else:
        standalone = 0
    return {
        "clusters": len(config.clusters),
        "clustered_services": clustered,
        "standalone_repositories": standalone,
        "repositories": len(config.repositories),
    }


def format_summary(config: RepositoryConfig, *, wrote: bool, unchanged: bool, path: Path | None) -> str:
    counts = generation_summary(config)
    name = path.name if path is not None else "cluster-manifest.json"
    if wrote:
        heading = f"Generated {name}"
    elif unchanged:
        heading = f"{name} is current"
    else:
        heading = name
    lines = [
        heading,
        "",
        f"{counts['clusters']} clusters",
        f"{counts['clustered_services']} clustered services",
        f"{counts['standalone_repositories']} standalone repositories",
        f"{counts['repositories']} repositories total",
    ]
    return "\n".join(lines)


def _write_if_needed(config: RepositoryConfig, destination: Path, *, replace_existing: bool) -> PrepareResult:
    rendered = render_manifest(config)
    if destination.is_file():
        existing = destination.read_text(encoding="utf-8")
        if existing == rendered:
            return PrepareResult(config=config, path=destination, unchanged=True)
        try:
            payload = json.loads(existing)
        except json.JSONDecodeError:
            payload = None
        if not replace_existing and not (isinstance(payload, dict) and manifest_is_generated(payload)):
            return PrepareResult(config=config, notices=[USER_MANAGED_NOTICE], path=destination)
        if isinstance(payload, dict) and manifest_is_generated(payload):
            current = str((payload.get("generated") or {}).get("source_hash") or "")
            if current and current == configuration_hash(config) and not replace_existing:
                return PrepareResult(config=config, path=destination, unchanged=True)
    _atomic_write(destination, rendered)
    return PrepareResult(config=config, wrote=True, path=destination)


def _atomic_write(destination: Path, rendered: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=destination.parent, prefix=f".{destination.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise
