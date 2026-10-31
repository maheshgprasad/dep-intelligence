"""Read published intelligence for a report. This module does not refresh or rank."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from dep_intel.app_state import cross_service_relationships
from dep_intel.config import Settings
from dep_intel.graph_models import CODE_SYMBOL, EVENT_CONTRACT, GRPC_CONTRACT, HTTP_CONTRACT, UNRESOLVED, Snapshot
from dep_intel.path_hygiene import scrub_text
from dep_intel.reporting.models import STATUS_LABELS
from dep_intel.reporting.review_summary import summarize_reviews
from dep_intel.repository_identity import github_name, history_slug, load_index
from dep_intel.review_store import list_reviews
from dep_intel.store import read_json
from dep_intel.topology.discovery import load_effective

CONTRACTS = {HTTP_CONTRACT, GRPC_CONTRACT, EVENT_CONTRACT}
_KIND = {HTTP_CONTRACT: "http", GRPC_CONTRACT: "grpc", EVENT_CONTRACT: "event"}
_PROVENANCE = {
    "extracted": "extracted",
    "extractor": "extracted",
    "declared": "declared",
    "manifest": "declared",
    "openapi": "OpenAPI",
    "generated": "generated",
    "override": "override",
    "manual": "override",
}


def label(status: str) -> str:
    return STATUS_LABELS.get(status, status.replace("_", " ").capitalize() if status else "Unavailable")


def relationships(snapshot: Snapshot, service: str = "", members: set[str] | None = None) -> dict[str, Any]:
    payload = cross_service_relationships(snapshot, limit=None, service=service)
    rows = []
    for item in payload.get("relationships") or []:
        if members is not None and (item.get("from_service") not in members or item.get("to_service") not in members):
            continue
        provenance = _provenance(str(item.get("evidence") or ""))
        rows.append({**item, "provenance": provenance, "resolution": item.get("resolution") or ""})
    shown = rows[:100]
    return {
        "relationships": rows,
        "items": shown,
        "total": len(rows),
        "truncated": len(rows) > len(shown),
        "included": len(shown),
        "provenance": _counts(rows, "provenance"),
        "resolution": _counts(rows, "resolution"),
    }


def history_for(settings: Settings, repository: str = "", *, service_id: str = "") -> dict[str, Any]:
    slug, mapping = history_slug(settings, service_id=service_id, repository=repository)
    if not slug:
        return {
            "status": "unavailable",
            "label": label("unavailable"),
            "slug": "",
            "commit_count": None,
            "hotspots": [],
            "cochange_coverage": None,
            "temporal": "unavailable",
            "note": f"History unavailable: {mapping}",
        }
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
    return _workspace_health(_load_health(settings))


def health_for_repository(settings: Settings, repository: str, service_id: str = "") -> dict[str, Any]:
    """Repository health when the artifact records that repository. Otherwise say so."""
    data = _load_health(settings)
    if not any(isinstance(item, dict) for item in data.values()):
        return _workspace_health(data)
    keys = {item for item in (repository, github_name(repository), service_id, Path(repository).name) if item}
    matched = _filter_health(data, keys)
    if matched is None:
        note = "Workspace-wide health data is available, but a repository-specific breakdown is not recorded."
        return {
            "packages": {"status": "workspace_only", "label": "Workspace-wide", "detail": note, "finding_count": None},
            "updates": {"status": "workspace_only", "label": "Workspace-wide", "detail": note, "finding_count": None},
            "security": {"status": "workspace_only", "label": "Workspace-wide", "detail": note, "finding_count": None},
            "coverage": {"status": "workspace_only", "label": "Workspace-wide", "detail": note, "finding_count": None},
        }
    return matched


def _load_health(settings: Settings) -> dict[str, Any]:
    return {
        "packages": read_json(settings.output_dir, "dep_matrix.json"),
        "updates": read_json(settings.output_dir, "package_updates.json"),
        "security": read_json(settings.output_dir, "vulnerabilities.json"),
        "coverage": read_json(settings.output_dir, "test_coverage.json"),
    }


def _workspace_health(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "packages": _artifact(data.get("packages"), "package inventory"),
        "updates": _artifact(data.get("updates"), "package update state"),
        "security": _security(data.get("security")),
        "coverage": _artifact(data.get("coverage"), "test coverage"),
    }


def _filter_health(data: dict[str, Any], keys: set[str]) -> dict[str, Any] | None:
    if not keys:
        return None
    packages = _section_for_repo(data.get("packages"), keys, ("repositories", "repos"))
    security = _security_for_repo(data.get("security"), keys)
    coverage = _section_for_repo(data.get("coverage"), keys, ("repositories", "repos"))
    updates = _section_for_repo(data.get("updates"), keys, ("repositories", "repos"))
    if packages is None and security is None and coverage is None and updates is None:
        return None
    note = "Counts are limited to stored records for this repository."
    return {
        "packages": packages or {"status": "not_scanned", "label": label("not_scanned"), "detail": "No repository package inventory was stored.", "finding_count": None},
        "updates": updates or {"status": "not_scanned", "label": label("not_scanned"), "detail": "No repository update state was stored.", "finding_count": None},
        "security": security or {"status": "not_scanned", "label": label("not_scanned"), "detail": "No repository security scan was stored.", "finding_count": None},
        "coverage": coverage or {"status": "not_scanned", "label": label("not_scanned"), "detail": "No repository coverage artifact was stored.", "finding_count": None},
        "note": note,
    }


def _section_for_repo(payload: Any, keys: set[str], containers: tuple[str, ...]) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    for name in containers:
        section = payload.get(name)
        if isinstance(section, dict):
            for key, value in section.items():
                if _key_matches(str(key), keys):
                    if isinstance(value, list):
                        count = len(value)
                    elif isinstance(value, dict):
                        count = len(value.get("packages") or value.get("files") or [])
                    else:
                        count = 1
                    return {"status": "available", "label": label("available"), "detail": "A repository-specific artifact is stored.", "finding_count": count}
        if isinstance(section, list):
            matched = [item for item in section if isinstance(item, dict) and _key_matches(str(item.get("repository") or item.get("repo") or item.get("name") or ""), keys)]
            if matched:
                return {"status": "available", "label": label("available"), "detail": "A repository-specific artifact is stored.", "finding_count": len(matched)}
    return None


def _security_for_repo(payload: Any, keys: set[str]) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    findings = [item for item in payload.get("findings") or [] if isinstance(item, dict) and _key_matches(str(item.get("repo") or item.get("repository") or ""), keys)]
    scanned = [item for item in payload.get("scanned") or [] if isinstance(item, dict) and _key_matches(str(item.get("repo") or item.get("repository") or ""), keys)]
    if not findings and not scanned:
        return None
    return {
        "status": "available",
        "label": label("available"),
        "detail": f"{len(findings)} findings are stored for this repository.",
        "finding_count": len(findings),
        "advisories": len([item for item in findings if item.get("id")]),
        "hygiene": len([item for item in findings if not item.get("id")]),
    }


def _key_matches(value: str, keys: set[str]) -> bool:
    if not value:
        return False
    if value in keys:
        return True
    named = github_name(value)
    leaf = value.split("/")[-1]
    return bool(named and named in keys) or leaf in keys


def reviews_for(
    settings: Settings,
    service: str = "",
    repository: str = "",
    limit: int = 20,
    members: set[str] | None = None,
    snapshot: Snapshot | None = None,
) -> dict[str, Any]:
    del limit
    rows = list_reviews(settings.output_dir, limit=None)
    tokens = _scope_tokens(settings, snapshot, members, service, repository)
    matched = rows if not tokens else [row for row in rows if _review_matches(row, tokens, set())]
    known = tokens if members is not None else None
    return summarize_reviews(settings.output_dir, matched, members=members, known=known)


def _scope_tokens(
    settings: Settings,
    snapshot: Snapshot | None,
    members: set[str] | None,
    service: str,
    repository: str,
) -> set[str]:
    """Exact identities for a report scope, including the canonical graph slug."""
    seeds = set(members or [])
    if service:
        seeds.add(service)
    pairs: list[tuple[str, str]] = []
    if snapshot is not None and seeds:
        for service_id in sorted(seeds):
            part = snapshot.partitions.get(service_id)
            pairs.append((service_id, part.repository if part else ""))
    elif service or repository:
        pairs.append((service, repository))
    if not pairs:
        return set()
    tokens: set[str] = set()
    index = load_index(settings)
    for service_id, repo in pairs:
        tokens.add(service_id)
        tokens.add(repo)
        named = github_name(repo)
        if named:
            tokens.add(named)
            tokens.add(named.split("/")[-1])
        record = index.resolve(service_id=service_id, repository=repo, checkout=repo)
        if record is None:
            continue
        tokens.update(item for item in (record.slug, record.name, record.service_id, record.repository) if item)
        if record.checkout:
            tokens.add(Path(record.checkout).name)
    tokens.discard("")
    return tokens


def with_review_explanations(settings: Settings, document: dict[str, Any]) -> dict[str, Any]:
    """Stored reports are immutable. Explanations are written only when the report is generated."""
    del settings
    return document


def _plain(value: Any) -> str:
    return " ".join(scrub_text(str(value or "")).split())[:400]


def _review_matches(row: dict[str, Any], service_ids: set[str], repository_names: set[str]) -> bool:
    if not service_ids and not repository_names:
        return True
    service = str(row.get("service") or "")
    if service and service in service_ids:
        return True
    repository = str(row.get("repository") or "")
    leaf = Path(repository).name
    named = github_name(repository)
    if leaf and (leaf in service_ids or leaf in repository_names):
        return True
    if named and (named in repository_names or named.split("/")[-1] in service_ids):
        return True
    return False


def _counts(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        name = str(row.get(key) or "unknown")
        counts[name] = counts.get(name, 0) + 1
    return counts


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


def _provenance(status: str) -> str:
    key = (status or "").strip().lower()
    return _PROVENANCE.get(key, key or "source extraction")


def _slug(settings: Settings, repository: str, service_id: str = "") -> str:
    slug, _note = history_slug(settings, service_id=service_id, repository=repository)
    return slug


def topology_for(settings: Settings, members: set[str] | None = None) -> dict[str, Any]:
    """Provider discovery captured once. Code-graph unresolved symbols are not included."""
    payload = load_effective(settings)
    if not isinstance(payload, dict):
        return {
            "status": "not_scanned",
            "label": "Not scanned",
            "unresolved_observation_count": 0,
            "unique_unresolved_reference_count": 0,
            "resolved_count": 0,
            "items": [],
            "note": "Topology provider discovery: Not scanned. Code-graph unresolved symbols are not provider references.",
        }
    rows = [item for item in (payload.get("unresolved") or []) if isinstance(item, dict)]
    if members is not None:
        rows = [item for item in rows if _topology_member(item, members)]
    grouped: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for item in rows:
        key = (
            str(item.get("from") or item.get("service") or ""),
            str(item.get("method") or ""),
            str(item.get("path") or ""),
            str(item.get("host") or ""),
        )
        current = grouped.get(key)
        if current is None:
            grouped[key] = {**item, "observations": 1, "service": key[0], "host": key[3], "name": key[3] or key[2]}
        else:
            current["observations"] = int(current.get("observations") or 1) + 1
    items = sorted(grouped.values(), key=lambda item: (str(item.get("service") or ""), str(item.get("host") or ""), str(item.get("path") or "")))
    unique = len(items)
    resolved = len(payload.get("relationships") or [])
    status = "complete" if unique == 0 else "partial"
    return {
        "status": status,
        "label": label(status),
        "unresolved_observation_count": len(rows),
        "unique_unresolved_reference_count": unique,
        "resolved_count": resolved,
        "items": items[:50],
        "note": "Unresolved provider references come from stored topology discovery.",
    }


def _topology_member(item: dict[str, Any], members: set[str]) -> bool:
    source = str(item.get("from") or item.get("service") or "")
    return source in members or source.split("/")[-1] in members


def code_symbol_count(snapshot: Snapshot, service_id: str = "") -> int:
    return sum(
        1
        for node in snapshot.nodes.values()
        if node.category == UNRESOLVED and (not service_id or node.service_id == service_id)
    )


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
