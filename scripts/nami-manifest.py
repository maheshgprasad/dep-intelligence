#!/usr/bin/env python3
"""Validate or generate cluster-manifest.json from repos.txt."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

from dep_intel.cluster_manifest_generation import ensure_manifest  # noqa: E402
from dep_intel.config import Settings  # noqa: E402
from dep_intel.sources import RepoConfigError, load_repos  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    command = args[0] if args else ""
    if command not in {"validate", "generate", "show"}:
        print("Usage: python scripts/nami-manifest.py validate|generate|show")
        return 2
    settings = Settings.load()
    try:
        repos = load_repos(settings)
    except RepoConfigError as exc:
        print("Manifest invalid.")
        for error in exc.errors:
            print(error)
        return 1
    if command == "generate":
        ensure_manifest(settings)
    print(_summary(repos))
    return 0


def _summary(repos) -> str:
    grouped: dict[str, int] = {}
    standalone = 0
    for repo in repos:
        if repo.cluster:
            grouped[repo.cluster] = grouped.get(repo.cluster, 0) + 1
        else:
            standalone += 1
    clustered = sum(grouped.values())
    return "\n".join([
        "Manifest valid.",
        f"{len(grouped)} declared clusters",
        f"{clustered} clustered services",
        f"{standalone} standalone repositories",
        f"{len(repos)} repositories total",
    ])


if __name__ == "__main__":
    raise SystemExit(main())
