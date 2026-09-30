"""Dependency advisories from OSV for packages already found in the matrix."""

from __future__ import annotations

import httpx

from dep_intel.config import Settings
from dep_intel.manifests import packages_in
from dep_intel.sources import Workspace
from dep_intel.store import now, write_json

_ECOSYSTEM = {"npm": "npm", "pypi": "PyPI", "go": "Go"}


def scan_vulnerabilities(workspaces: list[Workspace], settings: Settings, client: httpx.Client) -> dict:
    findings = []
    for workspace in workspaces:
        for package in packages_in(workspace.files):
            if not package.version or package.ecosystem not in _ECOSYSTEM:
                continue
            for advisory in _query(package.ecosystem, package.name, package.version, client):
                findings.append({"repo": workspace.ref.name, **advisory})
    payload = {
        "meta": {"generated_at": now(), "source": "dep-intel", "total": len(findings)},
        "findings": findings,
    }
    write_json(settings.output_dir, "vulnerabilities.json", payload)
    return {"success": True, "message": f"OSV returned {len(findings)} advisories."}


def _query(ecosystem: str, name: str, version: str, client: httpx.Client) -> list[dict]:
    body = {
        "package": {"name": name, "ecosystem": _ECOSYSTEM[ecosystem]},
        "version": version.lstrip("^~v"),
    }
    try:
        response = client.post("https://api.osv.dev/v1/query", json=body, timeout=20)
    except httpx.HTTPError:
        return []
    if response.status_code != 200:
        return []
    found = []
    for vuln in (response.json().get("vulns") or [])[:5]:
        severity = ""
        for item in vuln.get("severity") or []:
            severity = item.get("score") or ""
            break
        found.append(
            {
                "ecosystem": ecosystem,
                "package": name,
                "version": version,
                "id": vuln.get("id") or "",
                "summary": vuln.get("summary") or vuln.get("details") or "",
                "severity": severity,
            }
        )
    return found
