#!/usr/bin/env python3
"""Generate and check cluster-manifest.json from repos.txt."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

from dep_intel.cluster_manifest_generation import (  # noqa: E402
    format_summary,
    generation_summary,
    prepare_repository_configuration,
)
from dep_intel.config import Settings  # noqa: E402
from dep_intel.repo_config import RepoConfigError, load_repo_config  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the Nami cluster manifest from repos.txt.")
    parser.add_argument("command", choices=["generate", "validate", "show"])
    parser.add_argument("--replace-existing", action="store_true", help="Replace a user-managed cluster-manifest.json.")
    args = parser.parse_args()
    settings = Settings.load()
    if args.command == "validate":
        return _validate(settings)
    if args.command == "show":
        return _show(settings)
    result = prepare_repository_configuration(
        settings,
        target=settings.root / "cluster-manifest.json",
        replace_existing=args.replace_existing,
    )
    if result.errors:
        print("\n".join(result.errors), file=sys.stderr)
        return 1
    if result.notices:
        print("\n".join(result.notices), file=sys.stderr)
        return 2
    if result.config is None:
        print("repos.txt could not be read.", file=sys.stderr)
        return 1
    print(format_summary(result.config, wrote=result.wrote, unchanged=result.unchanged, path=result.path))
    return 0


def _validate(settings: Settings) -> int:
    try:
        config = load_repo_config(settings)
    except RepoConfigError as exc:
        print("\n".join(exc.errors), file=sys.stderr)
        return 1
    counts = generation_summary(config)
    print(
        f"repos.txt is valid. {counts['clusters']} clusters, "
        f"{counts['clustered_services']} clustered services, "
        f"{counts['standalone_repositories']} standalone repositories, "
        f"{counts['repositories']} repositories total."
    )
    return 0


def _show(settings: Settings) -> int:
    try:
        config = load_repo_config(settings)
    except RepoConfigError as exc:
        print("\n".join(exc.errors), file=sys.stderr)
        return 1
    if not config.repositories:
        print("No repositories are declared.")
        return 0
    for cluster in config.clusters:
        print(f"[cluster:{cluster.id}]")
        for service_id in cluster.service_ids:
            print(f"  {service_id}")
    standalone = [item for item in config.repositories if item.standalone or (config.explicit and item.cluster_id is None)]
    if standalone:
        print("[standalone]")
        for item in standalone:
            print(f"  {item.service_id} = {item.repo.raw}")
    if not config.explicit:
        print("Legacy repository list. No cluster membership is declared.")
        for item in config.repositories:
            print(f"  {item.repo.raw}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
