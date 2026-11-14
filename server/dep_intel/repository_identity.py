"""Exact repository identity. Substring matching is not used."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from dep_intel.config import Settings
from dep_intel.store import read_json


@dataclass(frozen=True)
class RepositoryRecord:
    slug: str
    name: str
    checkout: str
    service_id: str
    repository: str


class RepositoryIndex:
    def __init__(self, records: list[RepositoryRecord]) -> None:
        self.by_service_id: dict[str, RepositoryRecord] = {}
        self.by_repository: dict[str, RepositoryRecord] = {}
        self.by_checkout: dict[str, RepositoryRecord] = {}
        self.by_slug: dict[str, RepositoryRecord] = {}
        self.by_name: dict[str, RepositoryRecord] = {}
        for record in records:
            if record.service_id:
                self.by_service_id.setdefault(record.service_id, record)
            if record.repository:
                self.by_repository.setdefault(record.repository, record)
            if record.checkout:
                self.by_checkout.setdefault(record.checkout, record)
            if record.slug:
                self.by_slug.setdefault(record.slug, record)
            if record.name:
                self.by_name.setdefault(record.name, record)

    def resolve(self, *, service_id: str = "", repository: str = "", checkout: str = "") -> RepositoryRecord | None:
        if service_id and service_id in self.by_service_id:
            return self.by_service_id[service_id]
        if repository and repository in self.by_repository:
            return self.by_repository[repository]
        if checkout and checkout in self.by_checkout:
            return self.by_checkout[checkout]
        if repository and repository in self.by_checkout:
            return self.by_checkout[repository]
        if service_id and service_id in self.by_slug:
            return self.by_slug[service_id]
        if repository and repository in self.by_slug:
            return self.by_slug[repository]
        named = github_name(repository)
        if named and named in self.by_name:
            return self.by_name[named]
        if repository and repository in self.by_name:
            return self.by_name[repository]
        if service_id and service_id in self.by_name:
            return self.by_name[service_id]
        return None


def load_index(settings: Settings) -> RepositoryIndex:
    payload = read_json(settings.output_dir, "repo_graphs.json") or {}
    records = []
    for repo in payload.get("repos") or []:
        if not isinstance(repo, dict):
            continue
        records.append(RepositoryRecord(
            slug=str(repo.get("slug") or ""),
            name=str(repo.get("name") or ""),
            checkout=str(repo.get("checkout") or repo.get("path") or ""),
            service_id=str(repo.get("service_id") or ""),
            repository=str(repo.get("repository") or repo.get("url") or ""),
        ))
    return RepositoryIndex(records)


def history_slug(settings: Settings, *, service_id: str = "", repository: str = "") -> tuple[str, str]:
    """Return (slug, note). An empty slug means the mapping could not be resolved."""
    record = load_index(settings).resolve(service_id=service_id, repository=repository, checkout=repository)
    if record and record.slug:
        return record.slug, ""
    for candidate in (service_id, repository):
        if _exact_directory(settings, candidate):
            return candidate, ""
    return "", "repository-to-history mapping could not be resolved."


def partition_service(settings: Settings, snapshot: Any, requested: str = "", checkout: Path | None = None) -> str:
    """Map a service id, checkout slug, repository URL, or checkout directory to a snapshot partition.

    GitHub checkouts keep an owner_repo slug. Cluster membership uses the service id from repos.txt.
    """
    if snapshot is None:
        return ""
    requested = (requested or "").strip()
    partitions = getattr(snapshot, "partitions", None) or {}
    if requested and requested in partitions:
        return requested
    aliases = {item for item in (requested, checkout.name if checkout is not None else "") if item}
    if aliases:
        from dep_intel.sources import load_repos

        for ref in load_repos(settings):
            names = {ref.slug, ref.service_id, ref.name, ref.raw}
            if aliases & names:
                aliases.update(item for item in names if item)
    for part in partitions.values():
        if part.service_id in aliases or part.repository in aliases:
            return part.service_id
    return ""


def resolve_repository_path(settings: Settings, repository: str) -> Path | None:
    """Map a known slug or repos.txt identity to a checkout.

    The request value is compared with server-side identities. It is never
    opened, resolved, or passed to git as a path.
    """
    text = (repository or "").strip()
    if not text:
        return None
    record = load_index(settings).resolve(service_id=text, repository=text, checkout=text)
    if record and record.checkout and Path(record.checkout).is_dir():
        return Path(record.checkout)
    from dep_intel.sources import load_repos

    for repo in load_repos(settings):
        if text not in {repo.raw, repo.slug, repo.name, repo.service_id}:
            continue
        if repo.path is not None and repo.path.is_dir():
            return repo.path
        checkout = settings.output_dir / "checkouts" / repo.slug
        if checkout.is_dir():
            return checkout
    return None


def github_name(value: str) -> str:
    text = (value or "").strip()
    if "://" in text:
        path = urlparse(text).path.strip("/")
        return path if path.count("/") == 1 else ""
    if text.count("/") == 1 and not text.startswith("/") and not text.startswith("\\"):
        return text
    return ""


def _exact_directory(settings: Settings, candidate: str) -> bool:
    if not candidate or "/" in candidate or "\\" in candidate or "://" in candidate:
        return False
    return (settings.output_dir / "graphs" / candidate).is_dir()
