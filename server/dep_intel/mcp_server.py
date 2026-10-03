"""stdio MCP server. Cursor, Claude, and IBM Bob all launch this same module."""

from __future__ import annotations

import json
from contextlib import contextmanager
from collections.abc import Iterator

import httpx
from mcp.server.fastmcp import FastMCP

from dep_intel.api_deps import detect_api_dependencies as detect_apis
from dep_intel.commits import analyze_commits
from dep_intel.config import Settings
from dep_intel.coverage import run_coverage
from dep_intel.cve import scan_cve_issues
from dep_intel.graph import build_graphs
from dep_intel.languages import detect
from dep_intel.manifests import build_matrix
from dep_intel.pipeline import run_all
from dep_intel.review import review
from dep_intel.security import generate_security_report
from dep_intel.sources import Workspace, analysis_sources
from dep_intel.updates import check_updates
from dep_intel.vulns import scan_vulnerabilities

mcp = FastMCP("dep-intel")


@contextmanager
def _open(repos_file: str = "", output_dir: str = "") -> Iterator[tuple[Settings, httpx.Client, list[Workspace]]]:
    settings = Settings.load(repos_file, output_dir)
    with httpx.Client(follow_redirects=True) as client:
        with analysis_sources(settings) as workspaces:
            yield settings, client, workspaces


def _message(result: dict) -> str:
    return str(result.get("message") or "Done.")


@mcp.tool()
def detect_language(repos_file: str = "", output_dir: str = "") -> str:
    """Detect languages for repositories in repos.txt. Writes language_detection.json."""
    with _open(repos_file, output_dir) as (settings, _client, workspaces):
        return _message(detect(workspaces, settings))


@mcp.tool()
def analyze_dependencies(repos_file: str = "", output_dir: str = "") -> str:
    """Build the cross-repo dependency matrix. Writes dep_matrix.json."""
    with _open(repos_file, output_dir) as (settings, _client, workspaces):
        return _message(build_matrix(workspaces, settings))


@mcp.tool()
def check_package_updates(repos_file: str = "", output_dir: str = "") -> str:
    """Classify npm and PyPI updates. Writes package_updates.json."""
    with _open(repos_file, output_dir) as (settings, client, workspaces):
        return _message(check_updates(workspaces, settings, client))


@mcp.tool()
def detect_api_dependencies(repos_file: str = "", output_dir: str = "") -> str:
    """Map HTTP clients to server routes. Writes api_dependencies.json."""
    with _open(repos_file, output_dir) as (settings, _client, workspaces):
        return _message(detect_apis(workspaces, settings))


@mcp.tool()
def review_code(repos_file: str = "", output_dir: str = "") -> str:
    """Find unused functions and undefined Python names. Writes code_review.json."""
    with _open(repos_file, output_dir) as (settings, _client, workspaces):
        return _message(review(workspaces, settings))


@mcp.tool()
def run_coverage(repos_file: str = "", output_dir: str = "") -> str:
    """Run pytest coverage for local Python repositories. Writes test_coverage.json."""
    with _open(repos_file, output_dir) as (settings, _client, workspaces):
        return _message(run_coverage(workspaces, settings))


@mcp.tool()
def scan_vulnerabilities(repos_file: str = "", output_dir: str = "") -> str:
    """Query OSV for known advisories. Writes vulnerabilities.json."""
    with _open(repos_file, output_dir) as (settings, client, workspaces):
        return _message(scan_vulnerabilities(workspaces, settings, client))


@mcp.tool()
def scan_cve_from_github_issues(repos_file: str = "", output_dir: str = "") -> str:
    """Collect Mend dependency-vulnerability GitHub issues. Writes cve_analysis.json."""
    with _open(repos_file, output_dir) as (settings, client, workspaces):
        return _message(scan_cve_issues(workspaces, settings, client))


@mcp.tool()
def generate_security_release_report(output_dir: str = "") -> str:
    """Write the security report from the latest vulnerability and CVE files."""
    settings = Settings.load("", output_dir)
    return _message(generate_security_report(settings))


@mcp.tool()
def build_repo_graph(repos_file: str = "", output_dir: str = "") -> str:
    """Build each repository with the code-review-graph MCP server. Writes repo_graphs.json and crg.json."""
    with _open(repos_file, output_dir) as (settings, _client, workspaces):
        return _message(build_graphs(workspaces, settings))


@mcp.tool()
def analyze_commit_history(repos_file: str = "", output_dir: str = "", since_days: int = 365) -> str:
    """Mine git history for churn and co-change. Writes cochange.json per repository."""
    with _open(repos_file, output_dir) as (settings, client, workspaces):
        return _message(analyze_commits(workspaces, settings, client, since_days))


@mcp.tool()
def run_dependency_scan(repos_file: str = "", output_dir: str = "") -> str:
    """Run the full scan in order: language, dependencies, updates, APIs, review, coverage, security, graph, commits."""
    result = run_all(repos_file, output_dir)
    lines = [str(result.get("message") or "")]
    for item in result.get("results") or []:
        lines.append(f"{item.get('tool')}: {item.get('message')}")
    return "\n".join(lines)


def _cluster(repos_file: str = "", output_dir: str = ""):
    from dep_intel.cluster_service import ClusterEngine

    return ClusterEngine(Settings.load(repos_file, output_dir))


@mcp.tool()
def build_cross_repo_graph(repos_file: str = "", output_dir: str = "") -> str:
    """Build the cross-repository structural graph from CRG databases and the cluster manifest."""
    engine = _cluster(repos_file, output_dir)
    try:
        result = engine.refresh()
    finally:
        engine.close()
    return json.dumps(result, default=str)


@mcp.tool()
def get_cross_repo_graph(repos_file: str = "", output_dir: str = "", service: str = "", limit: int = 50) -> str:
    """Return a compact cluster summary and a bounded graph slice."""
    engine = _cluster(repos_file, output_dir)
    try:
        payload = {"summary": engine.summary(), "page": engine.slice(service=service, limit=limit)}
    finally:
        engine.close()
    return json.dumps(payload, default=str)


@mcp.tool()
def analyze_cross_repo_impact(
    service: str = "",
    symbol: str = "",
    file: str = "",
    line: int = 0,
    contract_id: str = "",
    mode: str = "code_change",
    scenario: str = "",
    topology: str = "current",
    repos_file: str = "",
    output_dir: str = "",
) -> str:
    """Structural impact for a symbol, file position, or contract. Scores are not probabilities."""
    engine = _cluster(repos_file, output_dir)
    try:
        status, payload = engine.impact(
            {
                "service": service,
                "symbol": symbol,
                "file": file,
                "line": line or None,
                "contract_id": contract_id,
                "mode": mode,
                "scenario": scenario,
                "topology": topology,
            }
        )
        payload["http_status"] = status
    finally:
        engine.close()
    return json.dumps(payload, default=str)


@mcp.tool()
def get_contract_dependencies(repos_file: str = "", output_dir: str = "") -> str:
    """List HTTP, gRPC, and event contracts in the published cluster snapshot."""
    engine = _cluster(repos_file, output_dir)
    try:
        payload = engine.contracts()
    finally:
        engine.close()
    return json.dumps(payload, default=str)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
