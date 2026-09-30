"""Package update classification against npm and PyPI."""

from __future__ import annotations

import httpx
from packaging.version import InvalidVersion, Version

from dep_intel.config import Settings
from dep_intel.manifests import Package, packages_in
from dep_intel.sources import Workspace
from dep_intel.store import now, write_json


def check_updates(workspaces: list[Workspace], settings: Settings, client: httpx.Client) -> dict:
    buckets: dict[str, list[dict]] = {"major": [], "minor": [], "patch": []}
    for workspace in workspaces:
        for package in packages_in(workspace.files):
            latest = _latest(package, client)
            if not latest or not package.version:
                continue
            kind = classify(package.version, latest)
            if not kind:
                continue
            buckets[kind].append(
                {
                    "repo": workspace.ref.name,
                    "ecosystem": package.ecosystem,
                    "package": package.name,
                    "from": package.version.lstrip("^~v"),
                    "to": latest,
                }
            )
    summary = {name: len(items) for name, items in buckets.items()}
    summary["total"] = sum(summary.values())
    payload = {
        "meta": {"generated_at": now(), "source": "dep-intel", "total_updates": summary["total"]},
        "updates": buckets,
        "summary": summary,
    }
    write_json(settings.output_dir, "package_updates.json", payload)
    return {
        "success": True,
        "message": (
            f"Found {summary['total']} updates: {summary['major']} major, "
            f"{summary['minor']} minor, {summary['patch']} patch."
        ),
        "summary": summary,
    }


def classify(current: str, latest: str) -> str | None:
    try:
        old = Version(_clean(current))
        new = Version(_clean(latest))
    except InvalidVersion:
        return None
    if new <= old:
        return None
    if new.major > old.major:
        return "major"
    if new.minor > old.minor:
        return "minor"
    return "patch"


def _clean(value: str) -> str:
    return value.strip().lstrip("^~=vV")


def _latest(package: Package, client: httpx.Client) -> str:
    try:
        if package.ecosystem == "npm":
            response = client.get(f"https://registry.npmjs.org/{package.name}/latest", timeout=15)
            if response.status_code == 200:
                return str(response.json().get("version") or "")
        if package.ecosystem == "pypi":
            response = client.get(f"https://pypi.org/pypi/{package.name}/json", timeout=15)
            if response.status_code == 200:
                return str(response.json().get("info", {}).get("version") or "")
    except httpx.HTTPError:
        return ""
    return ""
