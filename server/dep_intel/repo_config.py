"""Parse repos.txt into repository identity and declared cluster membership.

``repos.txt`` is the only file a user maintains for repositories, stable
service ids, and service-cluster membership. A line without ``=`` is a legacy
ungrouped repository. Sections declare clusters and standalone repositories.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from dep_intel.config import Settings
from dep_intel.sources import RepoRef, parse_repo_line

_CLUSTER = re.compile(r"^\[cluster:([^\]]*)\]$")
_STANDALONE = re.compile(r"^\[standalone\]$")
_ASSIGN = re.compile(r"^([^=]*?)\s*=\s*(.*)$")


class RepoConfigError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("\n".join(errors))


def valid_identifier(value: str) -> bool:
    return bool(value) and value[0].isalpha() and all(ch.isalnum() or ch in "-_" for ch in value)


def legacy_service_id(repo: RepoRef) -> str:
    slug = repo.slug or "repo"
    if valid_identifier(slug):
        return slug
    return f"repo-{slug}"


@dataclass
class RepoDeclaration:
    service_id: str
    repo: RepoRef
    cluster_id: str | None
    standalone: bool
    line: int


@dataclass
class ClusterDeclaration:
    id: str
    service_ids: list[str] = field(default_factory=list)
    line: int = 0


@dataclass
class RepositoryConfig:
    repositories: list[RepoDeclaration]
    clusters: list[ClusterDeclaration]
    source_path: Path
    explicit: bool = False


def load_repo_config(settings: Settings) -> RepositoryConfig:
    path = settings.repos_file
    if not path.is_file():
        return RepositoryConfig(repositories=[], clusters=[], source_path=path, explicit=False)
    return parse_repo_config(path.read_text(encoding="utf-8"), path, settings.root)


def parse_repo_config(text: str, source: Path, root: Path) -> RepositoryConfig:
    name = source.name
    errors: list[str] = []
    repositories: list[RepoDeclaration] = []
    clusters: list[ClusterDeclaration] = []
    section: str | None = None
    cluster: ClusterDeclaration | None = None
    explicit = False
    seen_ids: dict[str, tuple[int, str | None]] = {}
    seen_repos: dict[str, tuple[int, str, str | None]] = {}
    open_clusters: list[ClusterDeclaration] = []

    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        cluster_match = _CLUSTER.fullmatch(line)
        standalone_match = _STANDALONE.fullmatch(line)
        if cluster_match or standalone_match or (line.startswith("[") and line.endswith("]")):
            if repositories and not explicit:
                errors.append(f"{name}:{number}: cluster sections cannot be combined with legacy repository lines")
            explicit = True
            if cluster_match:
                cluster_id = cluster_match.group(1).strip()
                if not valid_identifier(cluster_id):
                    errors.append(f"{name}:{number}: invalid cluster id {cluster_id!r}")
                    section = "cluster"
                    cluster = None
                    continue
                if any(item.id == cluster_id for item in clusters):
                    errors.append(f"{name}:{number}: duplicate cluster id {cluster_id!r}")
                    section = "cluster"
                    cluster = None
                    continue
                cluster = ClusterDeclaration(id=cluster_id, line=number)
                clusters.append(cluster)
                open_clusters.append(cluster)
                section = "cluster"
                continue
            if standalone_match:
                section = "standalone"
                cluster = None
                continue
            errors.append(f"{name}:{number}: malformed section {line!r}")
            section = None
            cluster = None
            continue
        if "=" in line:
            explicit = True
            left, right = _split_assignment(line)
            if section not in {"cluster", "standalone"}:
                errors.append(f"{name}:{number}: malformed assignment")
                continue
            if not valid_identifier(left):
                errors.append(f"{name}:{number}: malformed assignment")
                continue
            if not right:
                errors.append(f"{name}:{number}: missing repository reference")
                continue
            if section == "cluster" and cluster is None:
                errors.append(f"{name}:{number}: malformed assignment")
                continue
            try:
                repo = parse_repo_line(right, root)
            except ValueError as exc:
                errors.append(f"{name}:{number}: {exc}")
                continue
            cluster_id = cluster.id if section == "cluster" and cluster is not None else None
            _remember(
                name,
                errors,
                seen_ids,
                seen_repos,
                repositories,
                number,
                left,
                repo,
                cluster_id,
                standalone=section == "standalone",
            )
            if cluster is not None and section == "cluster" and not _already_failed(errors, number):
                cluster.service_ids.append(left)
            continue
        if explicit:
            errors.append(f"{name}:{number}: malformed assignment")
            continue
        try:
            repo = parse_repo_line(line, root)
        except ValueError as exc:
            errors.append(f"{name}:{number}: {exc}")
            continue
        service_id = legacy_service_id(repo)
        _remember(
            name,
            errors,
            seen_ids,
            seen_repos,
            repositories,
            number,
            service_id,
            repo,
            None,
            standalone=False,
        )

    for item in open_clusters:
        if item.id and any(cluster.id == item.id and cluster is not item for cluster in clusters):
            continue
        if len(item.service_ids) == 0:
            errors.append(f"{name}:{item.line}: empty cluster {item.id!r}")
        elif len(item.service_ids) < 2:
            errors.append(f"{name}:{item.line}: cluster {item.id!r} must contain at least two services")
    if errors:
        raise RepoConfigError(errors)
    return RepositoryConfig(
        repositories=repositories,
        clusters=[item for item in clusters if item.service_ids],
        source_path=source,
        explicit=explicit,
    )


def _split_assignment(line: str) -> tuple[str, str]:
    matched = _ASSIGN.fullmatch(line)
    if matched is None:
        return "", ""
    return matched.group(1).strip(), matched.group(2).strip()


def _already_failed(errors: list[str], number: int) -> bool:
    needle = f":{number}:"
    return any(needle in error for error in errors)


def _remember(
    name: str,
    errors: list[str],
    seen_ids: dict[str, tuple[int, str | None]],
    seen_repos: dict[str, tuple[int, str, str | None]],
    repositories: list[RepoDeclaration],
    number: int,
    service_id: str,
    repo: RepoRef,
    cluster_id: str | None,
    *,
    standalone: bool,
) -> None:
    previous = seen_ids.get(service_id)
    if previous is not None:
        previous_line, previous_cluster = previous
        if previous_cluster and cluster_id and previous_cluster != cluster_id:
            errors.append(
                f"{name}:{number}: service {service_id!r} cannot belong to both clusters {previous_cluster!r} and {cluster_id!r}"
            )
        else:
            errors.append(f"{name}:{number}: duplicate service id {service_id!r} (first declared on line {previous_line})")
        return
    key = _repository_key(repo)
    previous_repo = seen_repos.get(key)
    if previous_repo is not None:
        previous_line, previous_raw, previous_cluster = previous_repo
        if previous_cluster and cluster_id and previous_cluster != cluster_id:
            errors.append(
                f"{name}:{number}: repository {repo.raw!r} cannot belong to both clusters {previous_cluster!r} and {cluster_id!r}"
            )
        else:
            errors.append(
                f"{name}:{number}: duplicate repository {repo.raw!r} (first declared on line {previous_line} as {previous_raw!r})"
            )
        return
    seen_ids[service_id] = (number, cluster_id)
    seen_repos[key] = (number, repo.raw, cluster_id)
    repositories.append(
        RepoDeclaration(
            service_id=service_id,
            repo=repo,
            cluster_id=cluster_id,
            standalone=standalone,
            line=number,
        )
    )


def _repository_key(repo: RepoRef) -> str:
    if repo.kind == "github":
        return f"github:{(repo.host or '').lower()}:{(repo.owner or '').lower()}/{(repo.repo or '').lower()}"
    if repo.path is not None:
        return f"path:{repo.path.resolve()}"
    return f"raw:{repo.raw}"
