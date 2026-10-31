"""Read published intelligence for a report. This module does not refresh or rank."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from dep_intel.app_state import cross_service_relationships
from dep_intel.config import Settings
from dep_intel.graph_models import CODE_SYMBOL, EVENT_CONTRACT, GRPC_CONTRACT, HTTP_CONTRACT, UNRESOLVED, Snapshot
from dep_intel.reporting.models import STATUS_LABELS
from dep_intel.review_store import list_reviews, read_review
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
    explained = [_explain_review(settings, row) for row in bounded]
    return {
        "total_considered": len(matched),
        "included": len(bounded),
        "explained": len(explained),
        "counts": counts,
        "reviews": explained,
        "note": "Review counts describe Nami Gate outcomes and are not a software-quality score. Each review below states why that outcome was reached and which stored evidence supports it.",
    }


_SOURCES = {
    "diff": "Code change",
    "CRG": "Code dependency graph",
    "cluster_graph": "Cross-service dependency graph",
    "OpenAPI": "API specification",
    "git_history": "Repository history",
    "verification_command": "Verification command",
    "policy": "Project rule",
    "NRM": "Nami Rank",
    "LLM": "Model suggestion",
}
_VALIDATION = {
    "validated": "Decision logic validated. The stored evidence and configured rules reproduced the same Gate result. This is not a proof of the production outcome.",
    "validated_with_limitations": "Decision logic validated with limitations. The Gate result was reproduced, but an optional check could not be completed.",
    "failed": "Decision validation failed. Do not rely on this decision until the mismatch is reviewed.",
}


def with_review_explanations(settings: Settings, document: dict[str, Any]) -> dict[str, Any]:
    """Add why, evidence, and validation to review rows that only stored a count."""
    activity = document.get("review_activity") if isinstance(document.get("review_activity"), dict) else {}
    reviews = activity.get("reviews") if isinstance(activity.get("reviews"), list) else []
    if reviews and all(isinstance(item, dict) and item.get("what_happened") for item in reviews):
        return document
    identity = document.get("identity") if isinstance(document.get("identity"), dict) else {}
    if document.get("scope") == "repository":
        refreshed = reviews_for(settings, service=str(identity.get("service") or ""), repository=str(identity.get("repository") or ""))
    else:
        refreshed = reviews_for(settings)
    copy = dict(document)
    copy["review_activity"] = refreshed
    return copy


def _explain_review(settings: Settings, row: dict[str, Any]) -> dict[str, Any]:
    report = read_review(settings.output_dir, str(row.get("review_id") or "")) or {}
    commentary = report.get("commentary") if isinstance(report.get("commentary"), dict) else {}
    assessment = report.get("assessment") if isinstance(report.get("assessment"), dict) else {}
    validation = report.get("gate_validation") if isinstance(report.get("gate_validation"), dict) else {}
    status = str(validation.get("status") or "")
    happened = _plain(commentary.get("summary") or assessment.get("summary") or "")
    why = _plain(commentary.get("why_it_matters") or "")
    if why == happened:
        why = ""
    if not happened and not why:
        happened = "This stored review does not include an explanation of the Gate result."
    evidence = []
    catalog = report.get("evidence_catalog") if isinstance(report.get("evidence_catalog"), list) else []
    for item in catalog:
        if not isinstance(item, dict):
            continue
        statement = _plain(item.get("statement") or item.get("summary") or "")
        if not statement:
            continue
        evidence.append({"source": _SOURCES.get(str(item.get("source") or ""), "Stored evidence"), "what": statement})
        if len(evidence) >= 3:
            break
    if not evidence:
        findings = report.get("findings") if isinstance(report.get("findings"), list) else []
        for finding in findings:
            if not isinstance(finding, dict):
                continue
            statement = _plain(finding.get("claim") or finding.get("title") or "")
            if not statement:
                continue
            evidence.append({"source": _SOURCES.get(str(finding.get("source") or ""), "Stored evidence"), "what": statement})
            if len(evidence) >= 3:
                break
    repository = str(row.get("repository") or "")
    return {
        "review_id": row.get("review_id"),
        "decision": row.get("decision"),
        "service": row.get("service"),
        "repository": Path(repository).name if repository else "",
        "completed_at": row.get("completed_at"),
        "headline": _plain(commentary.get("headline") or assessment.get("headline") or ""),
        "what_happened": happened,
        "why_it_matters": why,
        "next_action": _plain(commentary.get("next_action") or assessment.get("recommended_next_step") or ""),
        "validation_status": status,
        "validation": _plain(commentary.get("validation_comment") or _VALIDATION.get(status) or "Decision validation was not recorded for this stored review."),
        "evidence": evidence,
    }


def _plain(value: Any) -> str:
    text = re.sub(r"/(?:home|tmp|var|Users)/\S+", lambda match: Path(match.group(0)).name, str(value or ""))
    return " ".join(text.split())[:400]


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
