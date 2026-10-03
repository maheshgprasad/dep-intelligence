"""Validated cluster-manifest.json.

The manifest enriches repositories that are already listed in repos.txt.
It does not replace the allowlist and it does not invent service ids from
directory names.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from dep_intel.paths import contained
from dep_intel.sources import RepoRef

SUPPORTED_SCHEMA = 1


class ManifestError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


class HttpSurface(BaseModel):
    model_config = ConfigDict(extra="forbid")
    origins: list[str] = Field(default_factory=list)
    host_aliases: list[str] = Field(default_factory=list)
    base_path: str = ""


class ClientBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    file: str
    client: str
    target_service: str
    base_url: str = ""


class HttpContractDecl(BaseModel):
    model_config = ConfigDict(extra="forbid")
    method: str
    path: str
    version: str = ""
    handler: str = ""


class GrpcCaller(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service: str
    symbol: str


class GrpcContractDecl(BaseModel):
    model_config = ConfigDict(extra="forbid")
    package: str
    service: str
    method: str
    version: str = ""
    implementation: str = ""
    callers: list[GrpcCaller] = Field(default_factory=list)


class EventConsumerDecl(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service: str
    symbol: str
    group: str = ""


class EventContractDecl(BaseModel):
    model_config = ConfigDict(extra="forbid")
    broker: str
    topic: str
    schema_version: str = ""
    producers: list[str] = Field(default_factory=list)
    consumers: list[EventConsumerDecl] = Field(default_factory=list)


class ExpectedRelation(BaseModel):
    """A declared expectation. It is not extracted evidence."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["http", "grpc", "event"]
    consumer: str
    provider: str = ""
    method: str = ""
    path: str = ""
    package: str = ""
    service: str = ""
    topic: str = ""
    broker: str = ""
    version: str = ""
    symbol: str = ""


class ContractDecls(BaseModel):
    model_config = ConfigDict(extra="forbid")
    http: list[HttpContractDecl] = Field(default_factory=list)
    grpc: list[GrpcContractDecl] = Field(default_factory=list)
    events: list[EventContractDecl] = Field(default_factory=list)
    openapi: list[dict[str, str]] = Field(default_factory=list)


class ServiceDecl(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    repository: str
    graph_db: str = ".code-review-graph/graph.db"
    frameworks: list[str] = Field(default_factory=list)
    language: str = ""
    http: HttpSurface = Field(default_factory=HttpSurface)
    client_bindings: list[ClientBinding] = Field(default_factory=list)
    contracts: ContractDecls = Field(default_factory=ContractDecls)
    expected: list[ExpectedRelation] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def explicit_id(cls, value: str) -> str:
        if not value or not value[0].isalpha() or any(not (ch.isalnum() or ch in "-_") for ch in value):
            raise ValueError(f"service id {value!r} must be an explicit identifier, not a directory name")
        return value

    @field_validator("graph_db")
    @classmethod
    def relative_graph_db(cls, value: str) -> str:
        path = Path(value)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"graph_db {value!r} must stay inside the repository")
        return value


class Weights(BaseModel):
    model_config = ConfigDict(extra="forbid")
    internal_dependency: float = 0.95
    http_contract: float = 0.8
    grpc_contract: float = 0.8
    event_contract: float = 0.4

    @model_validator(mode="after")
    def in_unit_interval(self) -> "Weights":
        for name, value in self.model_dump().items():
            if not isinstance(value, (int, float)) or not 0 <= float(value) <= 1:
                raise ValueError(f"weight {name} must be between 0 and 1")
        return self


class ImpactSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    threshold: float = 0.1
    epsilon: float = 1e-9
    max_hops: int = 64
    max_nodes: int = 10000
    max_edges_examined: int = 200000
    timeout_ms: int = 2000
    min_edge_confidence: float | None = None
    include_unknown_confidence: bool = True
    weights: Weights = Field(default_factory=Weights)

    @field_validator("threshold", "epsilon")
    @classmethod
    def non_negative(cls, value: float) -> float:
        if value < 0:
            raise ValueError("impact thresholds must be >= 0")
        return value

    @field_validator("max_hops", "max_nodes", "max_edges_examined", "timeout_ms")
    @classmethod
    def positive(cls, value: int) -> int:
        if value < 0:
            raise ValueError("impact budgets must be >= 0")
        return value


class WatchSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    debounce_ms: int = 500
    reconcile_seconds: int = 30


class WorkerSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repository_reads: int = Field(default=4, ge=1, le=32)
    crg_sessions: int = Field(default=2, ge=1, le=8)


class ClusterManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int
    services: list[ServiceDecl]
    impact: ImpactSettings = Field(default_factory=ImpactSettings)
    watch: WatchSettings = Field(default_factory=WatchSettings)
    workers: WorkerSettings = Field(default_factory=WorkerSettings)


def manifest_path(root: Path, override: str = "") -> Path | None:
    if override:
        path = Path(override)
        return path if path.is_absolute() else root / path
    default = root / "cluster-manifest.json"
    return default if default.is_file() else None


def load_manifest(path: Path | None, repos: list[RepoRef], root: Path) -> tuple[ClusterManifest | None, list[str]]:
    if path is None or not path.is_file():
        return None, []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return None, [f"{path}: invalid JSON ({exc.lineno}:{exc.colno})"]
    if not isinstance(payload, dict):
        return None, [f"{path}: manifest must be a JSON object"]
    version = payload.get("schema_version")
    if version != SUPPORTED_SCHEMA:
        return None, [f"{path}: unsupported schema_version {version!r}; supported version is {SUPPORTED_SCHEMA}"]
    try:
        manifest = ClusterManifest.model_validate(payload)
    except Exception as exc:
        return None, [f"{path}: {exc}"]
    errors = _cross_check(manifest, repos, root)
    if errors:
        return None, errors
    return manifest, []


def _cross_check(manifest: ClusterManifest, repos: list[RepoRef], root: Path) -> list[str]:
    errors: list[str] = []
    ids = [service.id for service in manifest.services]
    if len(ids) != len(set(ids)):
        dupes = sorted({item for item in ids if ids.count(item) > 1})
        errors.append(f"duplicate service ids: {', '.join(dupes)}")
    slugs: dict[str, str] = {}
    for repo in repos:
        previous = slugs.get(repo.slug)
        if previous and previous != repo.raw:
            errors.append(
                f"repository slug {repo.slug!r} collides between {previous!r} and {repo.raw!r}; "
                "outputs would overwrite each other"
            )
        slugs[repo.slug] = repo.raw
    resolved: dict[str, RepoRef] = {}
    for service in manifest.services:
        matches = _match_repo(service.repository, repos, root)
        if not matches:
            errors.append(
                f"service {service.id}: repository {service.repository!r} is not an entry in repos.txt. "
                "Add that checkout to the allowlist or change the manifest reference."
            )
            continue
        if len(matches) > 1:
            names = ", ".join(item.raw for item in matches)
            errors.append(f"service {service.id}: repository {service.repository!r} matches more than one allowlist entry ({names})")
            continue
        resolved[service.id] = matches[0]
        repo_root = matches[0].path
        if repo_root is not None:
            graph_path = (repo_root / service.graph_db).resolve()
            if not contained(graph_path, repo_root):
                errors.append(f"service {service.id}: graph_db escapes {repo_root}")
        for binding in service.client_bindings:
            if not _safe_relative(binding.file):
                errors.append(f"service {service.id}: client binding file {binding.file!r} escapes the repository")
        for item in service.contracts.openapi:
            file_name = item.get("file", "")
            if file_name and not _safe_relative(file_name):
                errors.append(f"service {service.id}: openapi file {file_name!r} escapes the repository")
    known = set(ids)
    for service in manifest.services:
        for binding in service.client_bindings:
            if binding.target_service not in known:
                errors.append(
                    f"service {service.id}: client binding targets missing service {binding.target_service!r}"
                )
        for relation in service.expected:
            if relation.consumer not in known:
                errors.append(f"service {service.id}: expected consumer {relation.consumer!r} is not a service id")
            if relation.provider and relation.provider not in known:
                errors.append(f"service {service.id}: expected provider {relation.provider!r} is not a service id")
        for contract in service.contracts.grpc:
            for caller in contract.callers:
                if caller.service not in known:
                    errors.append(
                        f"service {service.id}: gRPC caller service {caller.service!r} is not declared"
                    )
        for contract in service.contracts.events:
            for consumer in contract.consumers:
                if consumer.service not in known:
                    errors.append(
                        f"service {service.id}: event consumer service {consumer.service!r} is not declared"
                    )
    if errors:
        return errors
    manifest.__dict__["_resolved"] = resolved
    return []


def resolved_repo(manifest: ClusterManifest, service_id: str) -> RepoRef | None:
    resolved = manifest.__dict__.get("_resolved") or {}
    repo = resolved.get(service_id)
    return repo if isinstance(repo, RepoRef) else None


def _safe_relative(value: str) -> bool:
    path = Path(value)
    return not path.is_absolute() and ".." not in path.parts


def _match_repo(reference: str, repos: list[RepoRef], root: Path) -> list[RepoRef]:
    target = Path(reference)
    if not target.is_absolute():
        target = (root / target).resolve()
    matches: list[RepoRef] = []
    for repo in repos:
        if reference in {repo.raw, repo.name, repo.slug}:
            matches.append(repo)
            continue
        if repo.path is not None and repo.path.resolve() == target:
            matches.append(repo)
    # A raw path and a resolved path can both match the same repo. Collapse duplicates.
    unique: list[RepoRef] = []
    seen: set[str] = set()
    for repo in matches:
        if repo.raw in seen:
            continue
        seen.add(repo.raw)
        unique.append(repo)
    return unique


def applicable_manifest(
    path: Path | None, repos: list[RepoRef], root: Path
) -> tuple[ClusterManifest | None, list[str], list[str]]:
    """Choose the manifest that applies to the current allowlist.

    ``repos.txt`` is the repository list. ``cluster-manifest.json`` only adds
    contract bindings for repositories that are already allowed. The shipped
    fixture file is ignored, with a notice, when none of its services are on
    the allowlist. Structural graphs are then built from ``repos.txt`` and
    cross-repository links are not inferred from route names.
    """
    if path is None or not path.is_file():
        return manifest_from_repos(repos), [], []
    manifest, errors = load_manifest(path, repos, root)
    if manifest is not None:
        return manifest, [], []
    if _unused_example(path, repos, root):
        names = ", ".join(repo.name for repo in repos) or "none"
        return (
            manifest_from_repos(repos),
            [],
            [
                f"{path.name} is an optional contract map for local fixtures, and none of those "
                f"repositories are in repos.txt. It is not the list of repositories to analyze. "
                f"Graphs are read from repos.txt ({names}). Cross-repository links are not guessed "
                "from route names; add a service entry for an allowlisted repository when you want those links."
            ],
        )
    return None, errors, []


def manifest_from_repos(repos: list[RepoRef]) -> ClusterManifest:
    services: list[ServiceDecl] = []
    resolved: dict[str, RepoRef] = {}
    for repo in repos:
        service_id = _service_id(repo)
        services.append(ServiceDecl(id=service_id, repository=repo.raw))
        resolved[service_id] = repo
    manifest = ClusterManifest(schema_version=SUPPORTED_SCHEMA, services=services)
    manifest.__dict__["_resolved"] = resolved
    return manifest


def _service_id(repo: RepoRef) -> str:
    slug = repo.slug or "repo"
    if slug[0].isalpha() and all(ch.isalnum() or ch in "-_" for ch in slug):
        return slug
    return f"repo-{slug}"


def _unused_example(path: Path, repos: list[RepoRef], root: Path) -> bool:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        manifest = ClusterManifest.model_validate(payload)
    except (OSError, json.JSONDecodeError, Exception):
        return False
    if not manifest.services:
        return False
    errors = _cross_check(manifest, repos, root)
    unmatched = [error for error in errors if "is not an entry in repos.txt" in error]
    return len(unmatched) == len(manifest.services) and len(errors) == len(unmatched)


def suggestion_for_missing_manifest() -> dict[str, Any]:
    return {
        "code": "manifest_missing",
        "message": (
            "No cluster-manifest.json is loaded. Cross-repository links are not inferred from "
            "route names. Declare service ids, host aliases, and client bindings to resolve them."
        ),
    }
