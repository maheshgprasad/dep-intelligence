"""Mend-labelled GitHub issues, enriched from OSV when a CVE id is present."""

from __future__ import annotations

import re

import httpx

from dep_intel.config import Settings
from dep_intel.sources import RepoRef, Workspace
from dep_intel.store import now, write_json

_CVE = re.compile(r"CVE-\d{4}-\d+", re.I)


def scan_cve_issues(workspaces: list[Workspace], settings: Settings, client: httpx.Client) -> dict:
    issues = []
    for workspace in workspaces:
        if workspace.ref.kind != "github":
            continue
        issues.extend(_issues_for(workspace.ref, settings, client))
    payload = {
        "meta": {
            "generated_at": now(),
            "source": "dep-intel",
            "total_cves": len({item["cve"] for item in issues if item.get("cve")}),
        },
        "issues": issues,
        "summary": {
            "total_issues": len(issues),
            "github_repos": sum(1 for workspace in workspaces if workspace.ref.kind == "github"),
            "message": "GitHub issue scan applies to repository URLs. Local paths are skipped.",
        },
    }
    write_json(settings.output_dir, "cve_analysis.json", payload)
    return {"success": True, "message": f"Collected {len(issues)} Mend vulnerability issues."}


def _issues_for(ref: RepoRef, settings: Settings, client: httpx.Client) -> list[dict]:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "dep-intel"}
    token = settings.github_token if ref.host == "github.com" else (settings.ghe_token or settings.github_token)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    base = "https://api.github.com" if ref.host == "github.com" else f"https://{ref.host}/api/v3"
    query = f'repo:{ref.owner}/{ref.repo} "Mend: dependency security vulnerability" is:issue'
    try:
        response = client.get(
            f"{base}/search/issues",
            params={"q": query, "per_page": 20},
            headers=headers,
            timeout=20,
        )
    except httpx.HTTPError:
        return []
    if response.status_code != 200:
        return []
    found = []
    for item in response.json().get("items") or []:
        title = item.get("title") or ""
        cves = sorted(set(_CVE.findall(f"{title}\n{item.get('body') or ''}")))
        found.append(
            {
                "repo": ref.name,
                "title": title,
                "url": item.get("html_url") or "",
                "state": item.get("state") or "",
                "cve": cves[0] if cves else "",
                "cves": cves,
            }
        )
    return found
