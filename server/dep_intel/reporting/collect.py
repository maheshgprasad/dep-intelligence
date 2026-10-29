"""Read published intelligence for a report. This module does not refresh or rank."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from dep_intel.app_state import cross_service_relationships
from dep_intel.config import Settings
from dep_intel.graph_models import CODE_SYMBOL, EVENT_CONTRACT, GRPC_CONTRACT, HTTP_CONTRACT, UNRESOLVED, Snapshot
from dep_intel.reporting.models import STATUS_LABELS
from dep_intel.review_store import list_reviews
from dep_intel.store import read_json

CONTRACTS = {HTTP_CONTRACT, GRPC_CONTRACT, EVENT_CONTRACT}
_KIND = {HTTP_CONTRACT: "http", GRPC_CONTRACT: "grpc", EVENT_CONTRACT: "event"}
_PROVENANCE = {
    "extracted": "extracted",
    "extractor": "extracted",
    "declared": "declared",
    "manifest": "declared",
    "openapi": "extracted",
    "generated": "generated",
    "override": "override",
    "manual": "override",
}


def label(status: str) -> str:
    return STATUS_LABELS.get(status, status.replace("_", " ").capitalize() if status else "Unavailable")


def relationships(snapshot: Snapshot, service: str = "") -> dict[str, Any]:
    payload = cross_service_relationships(snapshot, limit=200, service=service)
    rows = []
    for item in payload.get("relationships") or []:
        provenance = _provenance(item.get("evidence") or "", item.get("resolution") or "")
        rows.append({**item, "provenance": provenance})
    return {
        "relationships": rows,
        "total": payload.get("total") or 0,
        "truncated": bool(payload.get("truncated")),
        "included": len(rows),
    }


def history_for(settings: Settings, repository: str) -> dict[str, Any]:
    slug = _slug(settings, repository)
    cochange = read_json(settings.output_dir / "graphs" / slug, "cochange.json") if slug else None
    temporal = read_json(settings.output_dir / "graphs" / slug, "temporal_evidence.json") if slug else None
    if not isinstance(cochange, dict) or not cochange.get("commit_count"):
        return {
            "status": "unavailable",
            "label": label("unavailable"),
            "slug": slug,
            "commit_count": None,
            "hotspots": [],
            "cochange_coverage": None,
            "temporal": "unavailable" if not isinstance(temporal, dict) else "available",
            "note": "Historical change association was not available. Association is not a dependency.",
        }
    hotspots = []
    for item in (cochange.get("hotspots") or cochange.get("files") or [])[:5]:
        if isinstance(item, dict):
            hotspots.append(str(item.get("path") or item.get("file") or item.get("name") or ""))
        elif isinstance(item, str):
            hotspots.append(item)
    return {
        "status": "available",
        "label": label("available"),
        "slug": slug,
        "commit_count": cochange.get("commit_count"),
        "hotspots": [item for item in hotspots if item][:5],
        "cochange_coverage": cochange.get("coverage"),
        "temporal": "available" if isinstance(temporal, dict) and temporal else "unavailable",
        "note": "Historical change association. This is not a structural dependency.",
    }


def health_for(settings: Settings) -> dict[str, Any]:
    packages = read_json(settings.output_dir, "dep_matrix.json")
    updates = read_json(settings.output_dir, "package_updates.json")
    security = read_json(settings.output_dir, "vulnerabilities.json")
    coverage = read_json(settings.output_dir, "test_coverage.json")
    return {
        "packages": _artifact(packages, "package inventory"),
        "updates": _artifact(updates, "package update state"),
        "security": _security(security),
        "coverage": _artifact(coverage, "test coverage"),
    }


def reviews_for(settings: Settings, service: str = "", repository: str = "", limit: int = 20) -> dict[str, Any]:
    rows = list_reviews(settings.output_dir, limit=100)
    needle_repo = Path(repository).name if repository else ""

    def matches(row: dict[str, Any]) -> bool:
        if service and row.get("service") == service:
            return True
        if needle_repo and needle_repo in str(row.get("repository") or ""):
            return True
        return not service and not repository

    matched = [row for row in rows if matches(row)]
    bounded = matched[:limit]
    counts = {"pass": 0, "warn": 0, "block": 0, "incomplete": 0}
    for row in bounded:
        decision = str(row.get("decision") or "").lower()
        if decision in counts:
            counts[decision] += 1
    return {
        "total_considered": len(matched),
        "included": len(bounded),
        "counts": counts,
        "reviews": [
            {
                "review_id": row.get("review_id"),
                "decision": row.get("decision"),
                "service": row.get("service"),
                "repository": row.get("repository"),
                "completed_at": row.get("completed_at"),
            }
            for row in bounded
        ],
        "note": "Review counts describe Nami Gate outcomes and are not a software-quality score.",
    }


def architecture_of(settings: Settings, snapshot: Snapshot, service_id: str) -> dict[str, Any]:
    part = snapshot.partitions[service_id]
    files = sorted({node.file_path for node in part.nodes.values() if node.file_path})
    symbols = [node for node in part.nodes.values() if node.category == CODE_SYMBOL]
    kinds: dict[str, int] = {}
    for node in symbols:
        kinds[node.kind or "symbol"] = kinds.get(node.kind or "symbol", 0) + 1
    crg = crg_counts(settings, part.repository)
    return {
        "status": "available" if part.quality == "ok" and not part.error else "partial" if symbols or files else "unavailable",
        "files": len(files),
        "symbols": len(symbols),
        "kinds": kinds,
        "internal_relationships": len(part.edges),
        "hubs": crg.get("hubs"),
        "communities": crg.get("communities"),
        "flows": crg.get("flows"),
        "quality": part.quality,
        "error": part.error or "",
    }


def contracts_of(snapshot: Snapshot, service_id: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    provided = []
    for node in snapshot.nodes.values():
        if node.service_id != service_id or node.category not in CONTRACTS:
            continue
        consumers = sorted({row["from_service"] for row in rows if row.get("contract_id") == node.key or row.get("contract") == (node.name or node.qualified_name)})
        provided.append({
            "name": node.name or node.qualified_name,
            "kind": _KIND.get(node.category, node.category),
            "identity": str(node.metadata.get("identity") or node.qualified_name or ""),
            "provenance": str(node.metadata.get("provenance") or node.metadata.get("source") or ""),
            "consumers": consumers,
        })
    consumed = []
    for row in rows:
        if row.get("from_service") != service_id:
            continue
        consumed.append({
            "name": row.get("contract"),
            "kind": row.get("kind"),
            "identity": row.get("contract_identity"),
            "provider": row.get("to_service"),
            "provenance": row.get("provenance"),
        })
    provided.sort(key=lambda item: (item["kind"], item["name"]))
    consumed.sort(key=lambda item: (item.get("provider") or "", item.get("name") or ""))
    return {"provided": provided, "consumed": consumed}


def directions(service_id: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    upstream = [row for row in rows if row.get("from_service") == service_id]
    downstream = [row for row in rows if row.get("to_service") == service_id]
    return {"upstream": upstream, "downstream": downstream}


def unresolved_of(snapshot: Snapshot, service_id: str = "") -> list[dict[str, Any]]:
    rows = []
    for node in snapshot.nodes.values():
        if node.category != UNRESOLVED:
            continue
        if service_id and node.service_id != service_id:
            continue
        rows.append({
            "service": node.service_id,
            "name": node.name or node.qualified_name,
            "identity": str(node.metadata.get("identity") or ""),
        })
    rows.sort(key=lambda item: (item["service"], item["name"]))
    return rows


def _provenance(status: str, resolution: str) -> str:
    key = (resolution or status or "").strip().lower()
    return _PROVENANCE.get(key, key or "source extraction")


def _slug(settings: Settings, repository: str) -> str:
    name = Path(repository).name
    graphs = read_json(settings.output_dir, "repo_graphs.json") or {}
    for repo in graphs.get("repos") or []:
        slug = str(repo.get("slug") or "")
        checkout = str(repo.get("checkout") or repo.get("path") or "")
        if slug == name or repo.get("name") == name or (name and name in checkout):
            return slug
    return name


def crg_counts(settings: Settings, repository: str) -> dict[str, Any]:
    slug = _slug(settings, repository)
    payload = read_json(settings.output_dir / "graphs" / slug, "crg.json") if slug else None
    if not isinstance(payload, dict):
        return {}
    counts = {}
    for key in ("hubs", "communities", "flows", "bridges"):
        value = payload.get(key)
        if isinstance(value, list):
            counts[key] = len(value)
    return counts


def _artifact(payload: Any, name: str) -> dict[str, Any]:
    if payload is None:
        return {"status": "not_scanned", "label": label("not_scanned"), "detail": f"No stored {name} scan."}
    if not isinstance(payload, dict):
        return {"status": "unavailable", "label": label("unavailable"), "detail": f"The stored {name} artifact could not be read."}
    return {"status": "available", "label": label("available"), "detail": f"A {name} artifact is stored.", "finding_count": _count(payload)}


def _security(payload: Any) -> dict[str, Any]:
    base = _artifact(payload, "security")
    if not isinstance(payload, dict):
        return base
    findings = [item for item in payload.get("findings") or [] if isinstance(item, dict)]
    advisories = [item for item in findings if item.get("id")]
    hygiene = [item for item in findings if not item.get("id")]
    note = "No findings in the stored scan." if not findings else f"{len(advisories)} advisories and {len(hygiene)} hygiene notes."
    return {**base, "advisories": len(advisories), "hygiene": len(hygiene), "detail": note}


def _count(payload: dict[str, Any]) -> int | None:
    for key in ("findings", "packages", "results", "repos"):
        value = payload.get(key)
        if isinstance(value, list):
            return len(value)
    return None
