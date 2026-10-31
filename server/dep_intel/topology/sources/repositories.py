"""Allowlisted repositories are the service catalogue. repos.txt is not a topology file."""

from __future__ import annotations

from dep_intel.sources import RepoRef


def services_from_repos(repos: list[RepoRef]) -> list[dict[str, str]]:
    rows = []
    for repo in repos:
        if repo.path is None or not repo.path.is_dir():
            continue
        rows.append({"service": repo.name, "repository": repo.raw, "path": str(repo.path)})
    return rows
