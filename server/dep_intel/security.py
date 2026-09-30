"""Security report from OSV advisories and any Mend GitHub issues already scanned."""

from __future__ import annotations

from dep_intel.config import Settings
from dep_intel.store import now, read_json, write_json


def generate_security_report(settings: Settings) -> dict:
    advisories = (read_json(settings.output_dir, "vulnerabilities.json") or {}).get("findings") or []
    issues = (read_json(settings.output_dir, "cve_analysis.json") or {}).get("issues") or []
    payload = {
        "meta": {"generated_at": now(), "source": "dep-intel"},
        "open_from_github_issues": [item for item in issues if item.get("state") != "closed"],
        "dependency_advisories": advisories,
        "summary": {
            "github_issues": len(issues),
            "advisories": len(advisories),
        },
    }
    write_json(settings.output_dir, "security_release_report.json", payload)
    lines = [
        "# Security report",
        "",
        f"Generated {payload['meta']['generated_at']}.",
        "",
        f"GitHub Mend issues: {len(issues)}",
        f"OSV advisories: {len(advisories)}",
        "",
        "## OSV advisories",
        "",
    ]
    if not advisories:
        lines.append("None.")
    for item in advisories:
        lines.append(
            f"- `{item.get('id')}` {item.get('package')}@{item.get('version')} in {item.get('repo')}: {item.get('summary')}"
        )
    lines.extend(["", "## Open GitHub issues", ""])
    open_issues = payload["open_from_github_issues"]
    if not open_issues:
        lines.append("None. Add GitHub repository URLs to repos.txt to scan Mend issues.")
    for item in open_issues:
        lines.append(f"- {item.get('cve') or 'CVE unknown'} {item.get('title')} ({item.get('url')})")
    markdown = "\n".join(lines) + "\n"
    (settings.output_dir / "security_release_report.md").write_text(markdown, encoding="utf-8")
    return {
        "success": True,
        "message": f"Security report written with {len(advisories)} advisories and {len(issues)} GitHub issues.",
    }
