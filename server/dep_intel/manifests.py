"""Manifest parsing and the dependency matrix."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore

from dep_intel.sources import Workspace
from dep_intel.store import now, write_json
from dep_intel.config import Settings


@dataclass(frozen=True)
class Package:
    ecosystem: str
    name: str
    version: str


def packages_in(files: dict[str, str]) -> list[Package]:
    found: list[Package] = []
    for path, text in files.items():
        name = path.rsplit("/", 1)[-1]
        if name == "package.json":
            found.extend(_npm(text))
        elif name == "requirements.txt":
            found.extend(_requirements(text))
        elif name == "pyproject.toml":
            found.extend(_pyproject(text))
        elif name == "go.mod":
            found.extend(_gomod(text))
    return _dedupe(found)


def build_matrix(workspaces: list[Workspace], settings: Settings) -> dict:
    by_repo: dict[str, list[dict]] = {}
    index: dict[tuple[str, str], dict[str, str]] = {}
    for workspace in workspaces:
        packages = packages_in(workspace.files)
        by_repo[workspace.ref.name] = [
            {"name": package.name, "version": package.version, "ecosystem": package.ecosystem}
            for package in packages
        ]
        for package in packages:
            index.setdefault((package.ecosystem, package.name), {})[workspace.ref.name] = package.version

    common = []
    unique = []
    mismatches = []
    for (ecosystem, name), versions in sorted(index.items()):
        entry = {"name": name, "ecosystem": ecosystem, "repos": sorted(versions)}
        if len(versions) > 1:
            common.append(entry)
            distinct = set(versions.values())
            if len(distinct) > 1:
                mismatches.append({**entry, "versions": versions})
        else:
            repo, version = next(iter(versions.items()))
            unique.append({"name": name, "ecosystem": ecosystem, "repo": repo, "version": version})

    payload = {
        "meta": {
            "generated_at": now(),
            "source": "dep-intel",
            "repos_file": str(settings.repos_file),
            "total_repos": len(workspaces),
            "total_packages": sum(len(items) for items in by_repo.values()),
        },
        "repositories": by_repo,
        "common_packages": common,
        "unique_packages": unique,
        "version_mismatches": mismatches,
    }
    write_json(settings.output_dir, "dep_matrix.json", payload)
    return {
        "success": True,
        "message": f"Analyzed {payload['meta']['total_repos']} repositories, found {payload['meta']['total_packages']} packages.",
        "summary": payload["meta"],
    }


def _npm(text: str) -> list[Package]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    found = []
    for field in ("dependencies", "devDependencies"):
        for name, version in (data.get(field) or {}).items():
            found.append(Package("npm", name, str(version)))
    return found


def _requirements(text: str) -> list[Package]:
    found = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        match = re.match(r"([A-Za-z0-9_.\-]+)\s*==\s*([A-Za-z0-9*_.\-]+)", line)
        if match:
            found.append(Package("pypi", match.group(1), match.group(2)))
        else:
            name = re.split(r"[<>=!\s\[]", line, maxsplit=1)[0]
            if name:
                found.append(Package("pypi", name, ""))
    return found


def _pyproject(text: str) -> list[Package]:
    try:
        data = tomllib.loads(text)
    except Exception:
        return []
    found = []
    for dep in data.get("project", {}).get("dependencies") or []:
        match = re.match(r"([A-Za-z0-9_.\-]+)\s*(?:==\s*([A-Za-z0-9*_.\-]+))?", str(dep))
        if match:
            found.append(Package("pypi", match.group(1), match.group(2) or ""))
    return found


def _gomod(text: str) -> list[Package]:
    found = []
    for match in re.finditer(r"^\s*([A-Za-z0-9_./\-]+)\s+v([0-9][^\s]*)", text, re.M):
        module = match.group(1)
        if module.startswith("go") and "." not in module:
            continue
        found.append(Package("go", module, match.group(2)))
    return found


def _dedupe(packages: list[Package]) -> list[Package]:
    seen: dict[tuple[str, str], Package] = {}
    for package in packages:
        seen[(package.ecosystem, package.name)] = package
    return list(seen.values())
