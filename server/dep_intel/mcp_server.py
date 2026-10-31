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
def analyze_commit_history(repos_file: str = "", output_dir: str = "", since_days: int = 365, until: str = "") -> str:
    """Mine git history for churn, co-change, and directional temporal_evidence.json."""
    with _open(repos_file, output_dir) as (settings, client, workspaces):
        return _message(analyze_commits(workspaces, settings, client, since_days, until or None))


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
def analyze_hybrid_impact(
    service: str = "",
    symbol: str = "",
    file: str = "",
    line: int = 0,
    contract_id: str = "",
    mode: str = "code_change",
    as_of: str = "",
    structural_weight: float = 0.7,
    temporal_weight: float = 0.3,
    cross_repo_window_days: int = 7,
    temporal_candidate_limit: int = 25,
    temporal_provider: str = "commit_history",
    experiment: str = "hybrid",
    repos_file: str = "",
    output_dir: str = "",
) -> str:
    """Nami Rank Module (NRM): rank change-impact candidates from structural and temporal evidence. Scores are not probabilities."""
    engine = _cluster(repos_file, output_dir)
    try:
        status, payload = engine.hybrid_impact(
            {
                "service": service,
                "symbol": symbol,
                "file": file,
                "line": line or None,
                "contract_id": contract_id,
                "mode": mode,
                "as_of": as_of or None,
                "temporal_provider": temporal_provider,
                "weights": {"structural": structural_weight, "temporal": temporal_weight},
                "cross_repo_window_days": cross_repo_window_days,
                "temporal_candidate_limit": temporal_candidate_limit,
                "experiment": experiment or "hybrid",
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


def _review_engine(repos_file: str = "", output_dir: str = ""):
    from dep_intel.cluster_service import ClusterEngine
    from dep_intel.review_engine import ReviewEngine

    settings = Settings.load(repos_file, output_dir)
    cluster = ClusterEngine(settings)
    return ReviewEngine(settings, cluster=cluster), cluster


@mcp.tool()
def build_change_view(
    repository: str = "",
    service: str = "",
    source_kind: str = "working_tree",
    base_ref: str = "",
    head_ref: str = "",
    commit: str = "",
    pr_url: str = "",
    repos_file: str = "",
    output_dir: str = "",
) -> str:
    """Build a normalized Nami Change View for a local, commit, range, or pull request change."""
    engine, cluster = _review_engine(repos_file, output_dir)
    try:
        report = engine.run(
            {
                "source": {"kind": source_kind, "repository": repository, "base_ref": base_ref, "head_ref": head_ref, "commit": commit, "pr_url": pr_url, "service_id": service},
                "service": service,
                "mode": "fast",
                "verify": False,
                "model": {"enabled": False},
            }
        )
    finally:
        cluster.close()
    return json.dumps(
        {
            "review_id": report.get("review_id"),
            "change": report.get("change"),
            "changed_files": report.get("changed_files"),
            "changed_symbols": report.get("changed_symbols"),
            "contract_changes": report.get("contract_changes"),
            "diagnostics": report.get("diagnostics"),
            "gate": report.get("gate"),
        },
        default=str,
    )


@mcp.tool()
def review_change(
    repository: str = "",
    service: str = "",
    source_kind: str = "working_tree",
    base_ref: str = "",
    head_ref: str = "",
    commit: str = "",
    pr_url: str = "",
    mode: str = "fast",
    verify: bool = True,
    model_enabled: bool = False,
    repos_file: str = "",
    output_dir: str = "",
) -> str:
    """Run Nami Boundary Gate. The decision is pass, warn, block, or incomplete. Scores are not probabilities."""
    engine, cluster = _review_engine(repos_file, output_dir)
    try:
        report = engine.run(
            {
                "source": {"kind": source_kind, "repository": repository, "base_ref": base_ref, "head_ref": head_ref, "commit": commit, "pr_url": pr_url, "service_id": service},
                "service": service,
                "mode": mode,
                "verify": verify,
                "model": {"enabled": model_enabled},
            }
        )
    finally:
        cluster.close()
    gate = report.get("gate") or {}
    findings = report.get("findings") or []
    blocking = [item for item in findings if item.get("finding_id") in set(gate.get("blocking_findings") or [])]
    warnings = [item for item in findings if item.get("finding_id") in set(gate.get("warning_findings") or [])]
    targets = [item for item in findings if item.get("finding_id") in set(gate.get("review_targets") or [])]
    return json.dumps(
        {
            "decision": gate.get("decision"),
            "reason_codes": gate.get("reason_codes"),
            "change": report.get("summary"),
            "repository": (report.get("change") or {}).get("repository"),
            "affected_repositories": (report.get("impact") or {}).get("affected_services") or [],
            "top_review_targets": targets[:8],
            "blocking_findings": blocking,
            "warnings": warnings[:12],
            "verification": report.get("verification"),
            "evidence_identifiers": [item.get("fingerprint") for item in findings],
            "report_path": str(Settings.load(repos_file, output_dir).output_dir / "reviews" / str(report.get("review_id")) / "report.md"),
            "review_id": report.get("review_id"),
            "score_note": report.get("score_note"),
            "limitations": report.get("limitations"),
        },
        default=str,
    )


@mcp.tool()
def get_review(review_id: str, repos_file: str = "", output_dir: str = "") -> str:
    """Read a stored Nami Boundary Gate review."""
    from dep_intel.review_store import read_review

    settings = Settings.load(repos_file, output_dir)
    report = read_review(settings.output_dir, review_id)
    if report is None:
        return json.dumps({"code": "review_not_found", "review_id": review_id})
    return json.dumps(report, default=str)


@mcp.tool()
def explain_review_finding(review_id: str, finding_id: str, repos_file: str = "", output_dir: str = "") -> str:
    """Return the evidence for one Boundary Gate finding."""
    engine, cluster = _review_engine(repos_file, output_dir)
    try:
        payload = engine.explain(review_id, finding_id)
    finally:
        cluster.close()
    return json.dumps(payload, default=str)


@mcp.tool()
def record_review_feedback(
    review_id: str,
    finding_fingerprint: str,
    status: str,
    comment: str = "",
    repos_file: str = "",
    output_dir: str = "",
) -> str:
    """Store explicit review feedback. This does not modify Nami Rank scores or weights."""
    engine, cluster = _review_engine(repos_file, output_dir)
    try:
        status_code, payload = engine.feedback(review_id, {"finding_fingerprint": finding_fingerprint, "status": status, "comment": comment})
    finally:
        cluster.close()
    payload["http_status"] = status_code
    return json.dumps(payload, default=str)


@mcp.tool()
def export_review(review_id: str, format: str = "markdown", repos_file: str = "", output_dir: str = "") -> str:
    """Write a stored review export and return its path. Formats are markdown and json. PDF stays a file download."""
    from dep_intel.review_export import ensure_export

    kind = "markdown" if format in {"markdown", "md"} else "json" if format == "json" else ""
    if not kind:
        return json.dumps({"code": "unsupported_format", "format": format, "formats": ["markdown", "json"]})
    settings = Settings.load(repos_file, output_dir)
    try:
        path = ensure_export(settings.output_dir, review_id, kind)
    except FileNotFoundError:
        return json.dumps({"code": "review_not_found", "review_id": review_id})
    return json.dumps({"review_id": review_id, "format": kind, "path": str(path)})


@mcp.tool()
def generate_intelligence_report(scope: str = "workspace", service: str = "", cluster_id: str = "", repos_file: str = "", output_dir: str = "") -> str:
    """Generate a workspace, service-cluster, or repository intelligence report. Scope cluster is a legacy alias for workspace. Does not refresh, rank, or run the gate."""
    from dep_intel.cluster_service import get_engine
    from dep_intel.reporting.builder import ReportError, generate_report

    settings = Settings.load(repos_file, output_dir)
    snapshot = get_engine(settings).current()
    try:
        document = generate_report(settings, snapshot, scope, service, cluster_id=cluster_id)
    except (ReportError, KeyError) as exc:
        return json.dumps({"code": "report_unavailable", "message": str(exc)})
    return json.dumps({
        "report_id": document["report_id"],
        "scope": document["scope"],
        "graph_version": document["reproducibility"]["graph_version"],
        "readiness": (document.get("executive_summary") or {}).get("readiness"),
    })


@mcp.tool()
def list_service_clusters(repos_file: str = "", output_dir: str = "") -> str:
    """List connected service clusters from confirmed cross-service calls. This is not a Kubernetes cluster list."""
    from dep_intel.cluster_service import get_engine
    from dep_intel.service_clusters import detect_service_clusters

    settings = Settings.load(repos_file, output_dir)
    snapshot = get_engine(settings).current()
    groups = detect_service_clusters(snapshot) if snapshot is not None else {"clusters": [], "standalone": []}
    return json.dumps({
        "clusters": [
            {
                "cluster_id": item["cluster_id"],
                "label": item["label"],
                "service_count": item["service_count"],
                "relationship_count": item["relationship_count"],
                "services": item["services"],
            }
            for item in groups["clusters"]
        ],
        "standalone": groups["standalone"],
    }, default=str)


@mcp.tool()
def get_intelligence_report(report_id: str, repos_file: str = "", output_dir: str = "") -> str:
    """Return a stored intelligence report. This does not regenerate it."""
    from dep_intel.reporting.store import read_report

    settings = Settings.load(repos_file, output_dir)
    document = read_report(settings.output_dir, report_id)
    if document is None:
        return json.dumps({"code": "report_not_found", "report_id": report_id})
    return json.dumps(document, default=str)


@mcp.tool()
def export_intelligence_report(report_id: str, format: str = "markdown", repos_file: str = "", output_dir: str = "") -> str:
    """Write a stored intelligence report export and return its path. Formats are markdown, json, and pdf. PDF is a file path, not bytes."""
    from dep_intel.reporting.package import ensure_export

    kind = {"markdown": "markdown", "md": "markdown", "json": "json", "pdf": "pdf"}.get(format, "")
    if not kind:
        return json.dumps({"code": "unsupported_format", "format": format, "formats": ["markdown", "json", "pdf"]})
    settings = Settings.load(repos_file, output_dir)
    try:
        path = ensure_export(settings.output_dir, report_id, kind)
    except FileNotFoundError:
        return json.dumps({"code": "report_not_found", "report_id": report_id})
    return json.dumps({"report_id": report_id, "format": kind, "path": str(path)})


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
