"""One OSV query for every package declared by repositories in repos.txt."""

from __future__ import annotations

import httpx
from packaging.version import InvalidVersion, Version

from dep_intel.config import Settings
from dep_intel.manifests import packages_in
from dep_intel.sources import Workspace
from dep_intel.store import now, write_json

_ECOSYSTEM = {"npm": "npm", "pypi": "PyPI", "go": "Go"}


def scan_vulnerabilities(workspaces: list[Workspace], settings: Settings, client: httpx.Client) -> dict:
    findings = []
    scanned = []
    for workspace in workspaces:
        for package in packages_in(workspace.files):
            if package.ecosystem not in _ECOSYSTEM:
                continue
            version = _concrete_version(package.version)
            compared = version
            if not version:
                compared = _latest(package.ecosystem, package.name, client)
            if not compared:
                scanned.append(_row(workspace.ref.name, package.ecosystem, package.name, package.version, 0, "unresolved"))
                findings.append(
                    {
                        "repo": workspace.ref.name,
                        "ecosystem": package.ecosystem,
                        "package": package.name,
                        "version": package.version,
                        "id": "",
                        "summary": "No exact version could be resolved for an OSV query.",
                        "severity": "",
                        "fixed": "",
                        "suggestion": f"Pin {package.name} to an exact version in {workspace.ref.name}, then scan it against OSV again.",
                    }
                )
                continue
            advisories = _query(package.ecosystem, package.name, compared, client)
            if not version and not advisories:
                scanned.append(_row(workspace.ref.name, package.ecosystem, package.name, compared, 0, "scanned"))
                findings.append(
                    {
                        "repo": workspace.ref.name,
                        "ecosystem": package.ecosystem,
                        "package": package.name,
                        "version": package.version or compared,
                        "id": "",
                        "summary": f"OSV reports no advisory for the current release {compared}.",
                        "severity": "",
                        "fixed": compared,
                        "suggestion": f"Pin {package.name} to {compared} in {workspace.ref.name}. OSV reports no advisory for that release.",
                    }
                )
                continue
            if not version:
                for advisory in advisories:
                    advisory["suggestion"] = (
                        f"{package.name} is unpinned in {workspace.ref.name}. "
                        + advisory["suggestion"]
                        + f" The current release is {compared}."
                    )
            scanned.append(_row(workspace.ref.name, package.ecosystem, package.name, compared, len(advisories), "scanned"))
            for advisory in advisories:
                findings.append({"repo": workspace.ref.name, **advisory})
    suggestions = [item for item in findings if item.get("suggestion")]
    payload = {
        "meta": {
            "generated_at": now(),
            "source": "osv.dev",
            "database": "https://osv.dev/",
            "scanned": len(scanned),
            "total": len([item for item in findings if item.get("id")]),
            "suggestions": len(suggestions),
        },
        "scanned": scanned,
        "findings": findings,
        "suggestions": suggestions,
    }
    write_json(settings.output_dir, "vulnerabilities.json", payload)
    return {
        "success": True,
        "message": f"Queried {len(scanned)} packages on OSV. {payload['meta']['total']} advisories, {len(suggestions)} suggestions.",
    }


def _row(repo: str, ecosystem: str, name: str, version: str, advisories: int, status: str) -> dict:
    return {
        "repo": repo,
        "ecosystem": ecosystem,
        "package": name,
        "version": version,
        "advisory_count": advisories,
        "status": status,
    }


def _latest(ecosystem: str, name: str, client: httpx.Client) -> str:
    try:
        if ecosystem == "pypi":
            response = client.get(f"https://pypi.org/pypi/{name}/json", timeout=20)
            if response.status_code == 200:
                return str((response.json().get("info") or {}).get("version") or "")
        elif ecosystem == "npm":
            response = client.get(f"https://registry.npmjs.org/{name}/latest", timeout=20)
            if response.status_code == 200:
                return str(response.json().get("version") or "")
        elif ecosystem == "go":
            response = client.get(f"https://proxy.golang.org/{name}/@latest", timeout=20)
            if response.status_code == 200:
                return str(response.json().get("Version") or "").lstrip("v")
    except (httpx.HTTPError, ValueError):
        return ""
    return ""


def _concrete_version(version: str) -> str:
    text = version.strip().lstrip("^~=v")
    if not text or any(token in text for token in ("*", " ", ",", "<", ">", "||")):
        return ""
    return text


def _query(ecosystem: str, name: str, version: str, client: httpx.Client) -> list[dict]:
    body = {"package": {"name": name, "ecosystem": _ECOSYSTEM[ecosystem]}, "version": version}
    try:
        response = client.post("https://api.osv.dev/v1/query", json=body, timeout=20)
    except httpx.HTTPError:
        return []
    if response.status_code != 200:
        return []
    found = []
    for vuln in response.json().get("vulns") or []:
        fixed = _fixed_version(vuln, name, version)
        advisory_id = vuln.get("id") or ""
        summary = vuln.get("summary") or vuln.get("details") or ""
        if fixed:
            suggestion = f"Upgrade {name} from {version} to {fixed} or later to address {advisory_id}."
        else:
            suggestion = f"OSV lists {advisory_id} for {name}@{version} and does not name a fixed release. Review it before the next release."
        found.append(
            {
                "ecosystem": ecosystem,
                "package": name,
                "version": version,
                "id": advisory_id,
                "summary": summary,
                "severity": _severity(vuln),
                "fixed": fixed,
                "suggestion": suggestion,
            }
        )
    return found


def _severity(vuln: dict) -> str:
    for item in vuln.get("severity") or []:
        score = item.get("score") or ""
        if score:
            return score
    database = vuln.get("database_specific") or {}
    return str(database.get("severity") or "")


def _fixed_version(vuln: dict, name: str, current: str) -> str:
    candidates = []
    for affected in vuln.get("affected") or []:
        package = (affected.get("package") or {}).get("name") or ""
        if package.lower() != name.lower():
            continue
        for block in affected.get("ranges") or []:
            for event in block.get("events") or []:
                fixed = event.get("fixed")
                if fixed:
                    candidates.append(str(fixed).lstrip("v"))
    if not candidates:
        return ""
    try:
        current_version = Version(current.lstrip("v"))
        newer = [item for item in candidates if Version(item) > current_version]
        pool = newer or candidates
        return min(pool, key=Version)
    except InvalidVersion:
        return candidates[0]
