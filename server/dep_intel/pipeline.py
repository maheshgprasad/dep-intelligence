"""Ordered analysis used by the HTTP API and by MCP tools."""

from __future__ import annotations

from collections.abc import Callable

import httpx

from dep_intel.api_deps import detect_api_dependencies
from dep_intel.commits import analyze_commits
from dep_intel.config import Settings
from dep_intel.coverage import run_coverage
from dep_intel.cve import scan_cve_issues
from dep_intel.graph import build_graphs
from dep_intel.languages import detect
from dep_intel.manifests import build_matrix
from dep_intel.review import review
from dep_intel.security import generate_security_report
from dep_intel.sources import load_repos, load_workspace
from dep_intel.updates import check_updates
from dep_intel.vulns import scan_vulnerabilities

Progress = Callable[[str, int, str], None]

PHASES = [
    "detect_language",
    "analyze_dependencies",
    "check_package_updates",
    "detect_api_dependencies",
    "review_code",
    "run_coverage",
    "scan_vulnerabilities",
    "scan_cve_from_github_issues",
    "generate_security_release_report",
    "build_repo_graph",
    "analyze_commit_history",
]


def run_all(
    repos_file: str = "",
    output_dir: str = "",
    on_progress: Progress | None = None,
    since_days: int = 365,
) -> dict:
    settings = Settings.load(repos_file, output_dir)
    with httpx.Client(follow_redirects=True) as client:
        workspaces = [load_workspace(repo, client, settings) for repo in load_repos(settings)]
        if not workspaces:
            return {
                "success": False,
                "message": f"No repositories in {settings.repos_file}. Add local paths or GitHub URLs.",
            }
        steps: list[tuple[str, Callable[[], dict]]] = [
            ("detect_language", lambda: detect(workspaces, settings)),
            ("analyze_dependencies", lambda: build_matrix(workspaces, settings)),
            ("check_package_updates", lambda: check_updates(workspaces, settings, client)),
            ("detect_api_dependencies", lambda: detect_api_dependencies(workspaces, settings)),
            ("review_code", lambda: review(workspaces, settings)),
            ("run_coverage", lambda: run_coverage(workspaces, settings)),
            ("scan_vulnerabilities", lambda: scan_vulnerabilities(workspaces, settings, client)),
            ("scan_cve_from_github_issues", lambda: scan_cve_issues(workspaces, settings, client)),
            ("generate_security_release_report", lambda: generate_security_report(settings)),
            ("build_repo_graph", lambda: build_graphs(workspaces, settings)),
            ("analyze_commit_history", lambda: analyze_commits(workspaces, settings, client, since_days)),
        ]
        results = []
        for index, (name, action) in enumerate(steps):
            percent = int(index / len(steps) * 100)
            if on_progress:
                on_progress(name, percent, f"Running {name.replace('_', ' ')}")
            results.append({"tool": name, **action()})
        if on_progress:
            on_progress("complete", 100, "Analysis complete")
        return {"success": True, "message": "Analysis complete.", "results": results}


def run_graphs(repos_file: str = "", output_dir: str = "", since_days: int = 365, on_progress: Progress | None = None) -> dict:
    settings = Settings.load(repos_file, output_dir)
    with httpx.Client(follow_redirects=True) as client:
        workspaces = [load_workspace(repo, client, settings) for repo in load_repos(settings)]
        if on_progress:
            on_progress("build_repo_graph", 20, "Updating code graphs")
        graphs = build_graphs(workspaces, settings)
        if on_progress:
            on_progress("analyze_commit_history", 70, "Mining commit history")
        commits = analyze_commits(workspaces, settings, client, since_days)
        if on_progress:
            on_progress("complete", 100, "Code graph updated")
        return {"success": True, "message": f"{graphs['message']} {commits['message']}"}
