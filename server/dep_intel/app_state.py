"""Lightweight application state and the lazy reads that used to ride on the full snapshot."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import httpx

from dep_intel.config import Settings
from dep_intel.graph_models import CONSUMED_BY, CONSUMES_GRPC, CONSUMES_HTTP, PRODUCES_EVENT, Snapshot
from dep_intel.store import read_json

CONTRACT_CATEGORIES = {"http_contract", "grpc_contract", "event_contract"}
CROSS_RELATIONS = {
    CONSUMES_HTTP: "http",
    CONSUMES_GRPC: "grpc",
    PRODUCES_EVENT: "event",
    CONSUMED_BY: "event",
}


def build_app_state(settings: Settings, summary: dict[str, Any] | None, running: bool) -> dict[str, Any]:
    graphs = read_json(settings.output_dir, "repo_graphs.json") or {"repos": []}
    repositories = []
    history_ready = 0
    history_missing: list[str] = []
    for repo in graphs.get("repos") or []:
        slug = str(repo.get("slug") or "")
        repositories.append({
            "slug": slug,
            "name": repo.get("name") or slug,
            "checkout": repo.get("checkout") or repo.get("path") or "",
            "error": repo.get("error"),
        })
        cochange = read_json(settings.output_dir / "graphs" / slug, "cochange.json") if slug else None
        sampled = bool((cochange or {}).get("commit_count"))
        if sampled:
            history_ready += 1
        else:
            history_missing.append(slug or repo.get("name") or "")
    services = list((summary or {}).get("services") or [])
    total = len(services) or len(repositories)
    graph_ready = sum(1 for service in services if service.get("quality") == "ok" and not service.get("error"))
    stale = [service.get("id") or service.get("service_id") for service in services if service.get("quality") not in {None, "ok"}]
    failed = [service.get("id") or service.get("service_id") for service in services if service.get("error")]
    if not summary or not summary.get("version"):
        graph_status = "unavailable"
        contract_status = "unavailable"
        extraction_complete = False
    elif failed:
        graph_status = "failed"
        contract_status = "partial"
        extraction_complete = True
    elif stale:
        graph_status = "stale"
        contract_status = "current"
        extraction_complete = True
    elif total and graph_ready == len(services) and services:
        graph_status = "current"
        contract_status = "current"
        extraction_complete = True
    elif services:
        graph_status = "partial"
        contract_status = "partial"
        extraction_complete = True
    else:
        graph_status = "unavailable"
        contract_status = "unavailable"
        extraction_complete = False
    if not repositories:
        history_status = "unavailable"
    elif history_ready == len(repositories):
        history_status = "current"
    elif history_ready == 0:
        history_status = "missing"
    else:
        history_status = "partial"
    if graph_status == "unavailable" and history_status in {"unavailable", "missing"}:
        overall = "unavailable"
    elif graph_status == "current" and history_status == "current" and contract_status == "current":
        overall = "current"
    elif graph_status == "stale":
        overall = "stale"
    else:
        overall = "partial"
    return {
        "schema_version": 1,
        "running": running,
        "repositories": repositories,
        "cluster": summary or {},
        "intelligence": {
            "overall": overall,
            "graph": {
                "status": graph_status,
                "ready_services": graph_ready,
                "total_services": len(services),
            },
            "history": {
                "status": history_status,
                "ready_services": history_ready,
                "total_services": len(repositories),
                "missing_services": [item for item in history_missing if item],
                "sampled": history_ready > 0,
            },
            "contracts": {
                "status": contract_status,
                "extraction_complete": extraction_complete,
                "count": int((summary or {}).get("contract_count") or 0),
            },
            "stale_services": [item for item in stale if item],
            "unresolved": int((summary or {}).get("unresolved_count") or 0),
            "refreshed_at": (summary or {}).get("generated_at"),
        },
        "health": {
            "packages": _artifact(settings, "dep_matrix.json", "package_updates.json"),
            "security": _artifact(settings, "vulnerabilities.json"),
            "tests": _artifact(settings, "test_coverage.json"),
        },
    }


def service_catalog(snapshot: Snapshot | None, service: str) -> dict[str, Any]:
    if snapshot is None:
        return {"graph_version": "", "service": service, "files": [], "contracts": [], "error": "no_snapshot"}
    if service and service not in snapshot.partitions:
        return {"graph_version": snapshot.version, "service": service, "files": [], "contracts": [], "error": "unknown_service"}
    files: dict[str, str] = {}
    contracts = []
    for node in snapshot.nodes.values():
        if service and node.service_id != service:
            continue
        if node.file_path and not str(node.file_path).startswith("/") and ".." not in Path(node.file_path).parts:
            files.setdefault(node.file_path, node.file_path)
        if node.category in CONTRACT_CATEGORIES:
            contracts.append({
                "id": node.key,
                "name": node.name or node.qualified_name,
                "category": node.category,
                "service_id": node.service_id,
            })
    return {
        "graph_version": snapshot.version,
        "service": service,
        "files": [{"path": path, "label": path} for path in sorted(files)],
        "contracts": sorted(contracts, key=lambda item: (item["service_id"], item["name"], item["id"])),
    }


def cross_service_relationships(snapshot: Snapshot | None, *, limit: int = 50, service: str = "", kind: str = "") -> dict[str, Any]:
    if snapshot is None:
        return {"relationships": []}
    rows = []
    for edge in snapshot.edges:
        relation_kind = CROSS_RELATIONS.get(edge.relation)
        if relation_kind is None:
            continue
        if kind and relation_kind != kind:
            continue
        source = snapshot.nodes.get(edge.source)
        target = snapshot.nodes.get(edge.target)
        if source is None or target is None or source.service_id == target.service_id:
            continue
        if service and service not in {source.service_id, target.service_id}:
            continue
        contract = target if target.category in CONTRACT_CATEGORIES else source
        rows.append({
            "from_service": source.service_id,
            "to_service": target.service_id,
            "kind": relation_kind,
            "contract_id": contract.key,
            "contract": contract.name or contract.qualified_name,
        })
    rows.sort(key=lambda item: (item["from_service"], item["to_service"], item["kind"], item["contract"]))
    bounded = max(1, min(int(limit or 50), 200))
    return {"relationships": rows[:bounded]}


def search_cluster(snapshot: Snapshot | None, query: str, limit: int = 20) -> dict[str, Any]:
    needle = (query or "").strip().lower()
    if snapshot is None or not needle:
        return {"results": []}
    hits = []
    for node in snapshot.nodes.values():
        if node.category == "index":
            continue
        haystack = "\n".join([node.name or "", node.qualified_name or "", node.file_path or "", node.service_id or ""]).lower()
        if needle not in haystack:
            continue
        hits.append({
            "key": node.key,
            "service_id": node.service_id,
            "name": node.name,
            "qualified_name": node.qualified_name,
            "file_path": node.file_path,
            "kind": node.kind,
            "line_start": node.line_start,
        })
    hits.sort(key=lambda item: (item["service_id"], item["file_path"] or "", item["name"] or "", item["key"]))
    return {"results": hits[: max(1, min(int(limit or 20), 50))]}


def repository_architecture(settings: Settings, slug: str) -> dict[str, Any] | None:
    graphs = read_json(settings.output_dir, "repo_graphs.json") or {"repos": []}
    repo = next((item for item in graphs.get("repos") or [] if item.get("slug") == slug), None)
    if repo is None:
        return None
    return {
        "repository": {
            "slug": slug,
            "name": repo.get("name") or slug,
            "checkout": repo.get("checkout") or repo.get("path") or "",
            "error": repo.get("error"),
        },
        "crg": read_json(settings.output_dir / "graphs" / slug, "crg.json"),
        "cochange": read_json(settings.output_dir / "graphs" / slug, "cochange.json"),
    }


def health_packages(settings: Settings) -> dict[str, Any]:
    return {"matrix": read_json(settings.output_dir, "dep_matrix.json"), "updates": read_json(settings.output_dir, "package_updates.json")}


def health_security(settings: Settings) -> dict[str, Any]:
    payload = read_json(settings.output_dir, "vulnerabilities.json") or {}
    findings = list(payload.get("findings") or []) if isinstance(payload, dict) else []
    advisories = []
    hygiene = []
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        if finding.get("id"):
            advisories.append(finding)
        else:
            hygiene.append(finding)
    return {
        "advisories": advisories,
        "hygiene": hygiene,
        "cves": read_json(settings.output_dir, "cve_analysis.json"),
        "summary": read_json(settings.output_dir, "security_release_report.json"),
    }


def health_tests(settings: Settings) -> dict[str, Any]:
    return {"coverage": read_json(settings.output_dir, "test_coverage.json")}


def security_report_path(settings: Settings) -> Path:
    return settings.output_dir / "security_release_report.md"


def run_repository_health(settings: Settings, sections: list[str] | None, on_progress: Callable[[str, int, str], None] | None = None) -> dict[str, Any]:
    """Packages, advisories, and coverage. This does not rebuild graphs or rank impact."""
    selected = [item for item in (sections or ["packages", "security", "tests"]) if item in {"packages", "security", "tests"}]
    if not selected:
        selected = ["packages", "security", "tests"]
    from dep_intel.coverage import run_coverage
    from dep_intel.manifests import build_matrix
    from dep_intel.security import generate_security_report
    from dep_intel.sources import analysis_sources
    from dep_intel.updates import check_updates
    from dep_intel.vulns import scan_vulnerabilities

    results = []
    with httpx.Client(follow_redirects=True) as client:
        with analysis_sources(settings) as workspaces:
            steps = []
            if "packages" in selected:
                steps.append(("packages", lambda: build_matrix(workspaces, settings)))
                steps.append(("updates", lambda: check_updates(workspaces, settings, client)))
            if "security" in selected:
                steps.append(("security", lambda: scan_vulnerabilities(workspaces, settings, client)))
                steps.append(("security_report", lambda: generate_security_report(settings)))
            if "tests" in selected:
                steps.append(("tests", lambda: run_coverage(workspaces, settings)))
            for index, (name, action) in enumerate(steps):
                percent = int(index / max(len(steps), 1) * 90)
                if on_progress:
                    on_progress(name, percent, f"Scanning {name.replace('_', ' ')}")
                try:
                    outcome = action()
                except Exception as exc:
                    outcome = {"success": False, "message": str(exc)}
                results.append({"section": name, **(outcome if isinstance(outcome, dict) else {"success": True})})
    if on_progress:
        on_progress("complete", 100, "Repository health scan finished")
    return {"status": "ok", "sections": selected, "results": results}


def _artifact(settings: Settings, *names: str) -> dict[str, Any]:
    stamps = []
    for name in names:
        path = settings.output_dir / name
        if path.is_file():
            stamps.append(path.stat().st_mtime)
    if not stamps:
        return {"available": False, "updated_at": None}
    latest = datetime.fromtimestamp(max(stamps), timezone.utc).isoformat()
    return {"available": True, "updated_at": latest}
