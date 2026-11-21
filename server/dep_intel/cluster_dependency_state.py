"""Private before/after dependency state for one cluster review.

Graph edges stored by Nami point from the consumer to the provider. This module
records provider contracts and consumer references separately, then emits
relationships in provider → consumer impact order. It does not publish a graph,
reset a checkout, or run Boundary Gate.
"""

from __future__ import annotations

import hashlib
import logging
import re
import subprocess
from datetime import timedelta
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from dep_intel.cluster_manifest import ClusterManifest, HttpSurface, ServiceDecl
from dep_intel.config import Settings
from dep_intel.contracts import build_contract_model, normalize_template
from dep_intel.path_hygiene import scrub_text
from dep_intel.paths import contained

log = logging.getLogger("nami.cluster.state")

_JS = {".js", ".jsx", ".mjs", ".cjs"}
_VERSION = re.compile(r"^v\d+$", re.IGNORECASE)
_HTTP_IDENTITY = re.compile(r"^http:([^:]*):([A-Za-z]+):(.+)$")
_HTTP_DISPLAY = re.compile(r"^(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(\S+)$", re.IGNORECASE)
_GRPC_IDENTITY = re.compile(r"^[A-Za-z_][\w.]*\.[A-Za-z_]\w*/[A-Za-z_]\w*$")


def canonical_contract_identity(item: dict[str, Any] | None) -> str:
    """One HTTP/gRPC/event identity for graph rows and child contract changes."""
    if not isinstance(item, dict):
        return ""
    method = str(item.get("method") or "").strip()
    path = str(item.get("path") or "").strip()
    if method and path and not str(item.get("package") or ""):
        return f"{method.upper()} {normalize_template(path)}"
    for key in ("contract_identity", "contract", "contract_id"):
        parsed = parse_contract_text(str(item.get(key) or ""))
        if parsed:
            return parsed
    package = str(item.get("package") or "")
    service = str(item.get("grpc_service") or "")
    operation = str(item.get("method") or "")
    if package and service and operation and not path:
        return f"{package}.{service}/{operation}"
    broker = str(item.get("broker") or "")
    topic = str(item.get("topic") or "")
    if broker or topic:
        version = str(item.get("version") or "")
        return "/".join(part for part in (broker, topic, version) if part)
    return ""


def parse_contract_text(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    http = _HTTP_IDENTITY.match(text)
    if http:
        return f"{http.group(2).upper()} {normalize_template(http.group(3))}"
    display = _HTTP_DISPLAY.match(text)
    if display:
        return f"{display.group(1).upper()} {normalize_template(display.group(2))}"
    if _GRPC_IDENTITY.match(text):
        return text
    return ""


def canonical_relationship_key(relationship_type: str, provider: str, consumer: str, identity: str) -> tuple[str, str, str, str]:
    return (relationship_type or "unknown", provider, consumer, identity)


def version_successor(old: str, new: str) -> bool:
    """True when two HTTP identities differ by exactly one vN path segment.

    A rename such as /login to /metrics is not a successor. Callers still have
    to see the same one-to-one provider and consumer delta before treating it
    as a migration.
    """
    old_method, old_path = _split_http(old)
    new_method, new_path = _split_http(new)
    if not old_method or old_method != new_method:
        return False
    left = [part for part in old_path.split("/") if part]
    right = [part for part in new_path.split("/") if part]
    if len(left) != len(right):
        return False
    changed = [(a, b) for a, b in zip(left, right, strict=True) if a != b]
    if len(changed) != 1:
        return False
    before, after = changed[0]
    return bool(_VERSION.match(before) and _VERSION.match(after) and before.lower() != after.lower())


def manifest_for_members(manifest: ClusterManifest | None, service_ids: list[str]) -> ClusterManifest:
    """Cluster-scoped manifest. Declared contracts stay; unrelated services do not."""
    by_id = {service.id: service for service in manifest.services} if manifest is not None else {}
    selected = []
    for service_id in service_ids:
        if service_id in by_id:
            selected.append(by_id[service_id])
        else:
            selected.extend(synthetic_manifest([service_id]).services)
    if len(selected) < 1:
        return synthetic_manifest(service_ids)
    return ClusterManifest(schema_version=1, services=selected)


def synthetic_manifest(service_ids: list[str]) -> ClusterManifest:
    services = [
        ServiceDecl(
            id=service_id,
            repository=service_id,
            http=HttpSurface(origins=[f"http://{service_id}"], host_aliases=[service_id]),
        )
        for service_id in service_ids
        if service_id
    ]
    return ClusterManifest(schema_version=1, services=services)


def load_cluster_manifest(settings: Settings) -> ClusterManifest | None:
    from dep_intel.cluster_manifest import load_manifest, manifest_path
    from dep_intel.sources import load_repos

    path = manifest_path(settings.root)
    manifest, errors = load_manifest(path, load_repos(settings), settings.root)
    for message in errors:
        log.warning("cluster manifest skipped: %s", scrub_text(message))
    return manifest


def build_cluster_dependency_state(
    service_roots: dict[str, Path | None],
    *,
    state_kind: str,
    source_kind: str,
    manifest: ClusterManifest,
    service_meta: dict[str, dict[str, Any]] | None = None,
    file_cache: dict[tuple[str, str], dict[str, str]] | None = None,
    cancel: Callable[[], bool] | None = None,
    on_service: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    """Build one cluster-wide contract state from the supplied repository roots."""
    meta = service_meta or {}
    cache = file_cache if file_cache is not None else {}
    members = [service_id for service_id in manifest.services if service_id.id in service_roots]
    if not members:
        members = [
            ServiceDecl(id=service_id, repository=service_id, http=HttpSurface(origins=[f"http://{service_id}"], host_aliases=[service_id]))
            for service_id in service_roots
        ]
        manifest = ClusterManifest(schema_version=1, services=members)
    files_by_service: dict[str, dict[str, str]] = {}
    services: dict[str, dict[str, Any]] = {}
    diagnostics: list[dict[str, str]] = []
    total = len(service_roots) or 1
    for index, service_id in enumerate(sorted(service_roots), start=1):
        if cancel is not None and cancel():
            return _cancelled_state(state_kind, source_kind)
        if on_service is not None:
            on_service(index, total)
        info = dict(meta.get(service_id) or {})
        root = service_roots.get(service_id)
        cache_key = str(info.get("cache_key") or "")
        exact = bool(info.get("exact", True))
        state_source = str(info.get("state_source") or "git_ref")
        if info.get("empty"):
            files_by_service[service_id] = {}
            exact = True
            state_source = str(info.get("state_source") or "repository_absent")
        elif root is None or not Path(root).is_dir():
            files_by_service[service_id] = {}
            exact = False
            state_source = "unavailable"
            diagnostics.append({
                "code": "repository_unavailable",
                "service_id": service_id,
                "message": "Dependency state could not be read for this repository.",
            })
        else:
            cached = cache.get((service_id, cache_key)) if cache_key else None
            if cached is None:
                try:
                    from dep_intel.sources import read_contract_sources

                    cached, truncated = read_contract_sources(Path(root))
                    if truncated:
                        diagnostics.append({
                            "code": "source_truncated",
                            "service_id": service_id,
                            "message": "Contract source reading stopped at the file budget.",
                        })
                except Exception:
                    log.exception("contract source read failed for %s", service_id)
                    cached = {}
                    exact = False
                    diagnostics.append({
                        "code": "state_extraction_failed",
                        "service_id": service_id,
                        "message": "Dependency state could not be read for this repository.",
                    })
                if cache_key:
                    cache[(service_id, cache_key)] = cached
            files_by_service[service_id] = cached
        services[service_id] = {
            "ref": str(info.get("ref") or ""),
            "state_source": state_source,
            "exact": exact,
        }
        if info.get("diagnostic"):
            services[service_id]["diagnostic"] = scrub_text(str(info.get("diagnostic")))
    model = build_contract_model(manifest, files_by_service)
    providers, consumers, relationships, model_notes = _relationships_from_model(manifest, model, files_by_service, set(service_roots))
    diagnostics.extend(model_notes)
    for note in model.diagnostics:
        if note.get("code") in {"route_unmatched", "dynamic_client_url", "unresolved_provider"}:
            continue
        diagnostics.append({
            "code": str(note.get("code") or "contract_model"),
            "service_id": str(note.get("service_id") or ""),
            "message": scrub_text(str(note.get("message") or ""))[:240],
        })
    body = {
        "state_kind": state_kind,
        "source_kind": source_kind,
        "services": services,
        "provider_contracts": providers,
        "consumer_references": consumers,
        "relationships": relationships,
        "diagnostics": diagnostics,
        "coverage": _coverage(services, relationships, manifest, set(service_roots)),
    }
    body["state_id"] = hashlib.sha256(_canonical_blob(body).encode("utf-8")).hexdigest()[:16]
    return body


def build_review_boundaries(
    members: list[dict[str, Any]],
    source: dict[str, Any],
    *,
    settings: Settings,
    work_dir: Path,
    cancel: Callable[[], bool] | None = None,
    on_progress: Callable[[str, int, int], None] | None = None,
) -> dict[str, Any]:
    """Materialize private before/after roots and build both cluster states."""
    service_ids = [str(item.get("service_id") or "") for item in members if item.get("service_id")]
    manifest = manifest_for_members(load_cluster_manifest(settings), service_ids)
    work_dir.mkdir(parents=True, exist_ok=True)
    file_cache: dict[tuple[str, str], dict[str, str]] = {}
    before_roots: dict[str, Path | None] = {}
    after_roots: dict[str, Path | None] = {}
    before_meta: dict[str, dict[str, Any]] = {}
    after_meta: dict[str, dict[str, Any]] = {}
    cleanup: list[tuple[Path, Path]] = []
    limitations: list[str] = []
    try:
        for member in members:
            if cancel is not None and cancel():
                return {"cancelled": True, "limitations": limitations}
            service_id = str(member.get("service_id") or "")
            repo = Path(str(member.get("repository_path") or ""))
            bounds = resolve_member_bounds(repo, source, member, settings=settings, work_dir=work_dir / _safe(service_id))
            for spec in (bounds["before"], bounds["after"]):
                if spec.get("owned") and spec.get("root") is not None:
                    cleanup.append((repo, Path(spec["root"])))
            before_roots[service_id] = bounds["before"].get("root")
            after_roots[service_id] = bounds["after"].get("root")
            before_meta[service_id] = bounds["before"]
            after_meta[service_id] = bounds["after"]
            member_refs = {
                "before_ref": bounds["before"].get("ref") or "",
                "after_ref": bounds["after"].get("ref") or "",
                "state_status": _pair_status(bounds["before"], bounds["after"]),
            }
            bounds["refs"] = member_refs
            member["_boundary"] = member_refs
        if cancel is not None and cancel():
            return {"cancelled": True, "limitations": limitations}

        def _progress(kind: str):
            def emit(done: int, total: int) -> None:
                if on_progress is not None:
                    on_progress(kind, done, total)
            return emit

        before_state = build_cluster_dependency_state(
            before_roots,
            state_kind="before",
            source_kind=str(source.get("kind") or ""),
            manifest=manifest,
            service_meta=before_meta,
            file_cache=file_cache,
            cancel=cancel,
            on_service=_progress("before"),
        )
        if before_state.get("cancelled"):
            return {"cancelled": True, "before_state": before_state, "limitations": limitations}
        after_state = build_cluster_dependency_state(
            after_roots,
            state_kind="after",
            source_kind=str(source.get("kind") or ""),
            manifest=manifest,
            service_meta=after_meta,
            file_cache=file_cache,
            cancel=cancel,
            on_service=_progress("after"),
        )
        if after_state.get("cancelled"):
            return {"cancelled": True, "before_state": before_state, "after_state": after_state, "limitations": limitations}
    finally:
        for repo, dest in cleanup:
            _cleanup_worktree(repo, dest)
    mode, coverage, limitations = _mode_for(before_state, after_state, manifest, set(service_ids))
    return {
        "cancelled": False,
        "before_state": before_state,
        "after_state": after_state,
        "comparison_mode": mode,
        "comparison_coverage": coverage,
        "limitations": limitations,
    }


def resolve_member_bounds(
    repo: Path,
    source: dict[str, Any],
    member: dict[str, Any],
    *,
    settings: Settings,
    work_dir: Path,
) -> dict[str, dict[str, Any]]:
    """Choose the private before and after roots for one cluster member."""
    kind = str(source.get("kind") or "working_tree")
    origin = _member_is_origin(repo, source, member)
    if not repo.is_dir() or not _git_ok(repo):
        missing = _spec(exact=False, state_source="unavailable", diagnostic="repository_unavailable")
        return {"before": missing, "after": dict(missing)}
    if kind == "working_tree":
        head = _head(repo)
        before = _materialized(repo, head, work_dir / "before", state_source="git_ref")
        if _clean(repo) and before.get("exact"):
            after = _spec(root=before.get("root"), ref=head, cache_key=before.get("cache_key") or head, state_source="git_ref", owned=False, exact=True)
        else:
            after = _spec(root=repo, ref=head, cache_key=f"worktree:{_fingerprint(repo)}", state_source="worktree", owned=False, exact=True)
        return {"before": before, "after": after}
    if kind == "staged":
        head = _head(repo)
        before = _materialized(repo, head, work_dir / "before", state_source="git_ref")
        if _staged_empty(repo) and before.get("exact"):
            after = _spec(root=before.get("root"), ref=head, cache_key=before.get("cache_key") or head, state_source="git_ref", owned=False, exact=True)
        else:
            after = _materialized(repo, head, work_dir / "after", state_source="staged")
            if after.get("exact") and after.get("root") is not None:
                applied, _message = _apply_staged(repo, Path(after["root"]))
                if not applied:
                    after["exact"] = False
                    after["state_source"] = "unavailable"
                    after["diagnostic"] = "staged_apply_failed"
                else:
                    after["cache_key"] = f"staged:{head}:{_staged_fingerprint(repo)}"
        return {"before": before, "after": after}
    if kind == "commit":
        if not origin:
            return _baseline_pair(repo, work_dir)
        sha = _verify(repo, str(source.get("commit") or ""))
        if not sha:
            missing = _spec(exact=False, state_source="unavailable", diagnostic="git_ref_not_found")
            return {"before": missing, "after": missing}
        parent = _parent(repo, sha)
        before = _empty_spec("repository_absent") if not parent else _materialized(repo, parent, work_dir / "before", state_source="git_ref")
        after = _materialized(repo, sha, work_dir / "after", state_source="git_ref")
        return {"before": before, "after": after}
    if kind == "range":
        if not origin:
            return _baseline_pair(repo, work_dir)
        base = _verify(repo, str(source.get("base_ref") or ""))
        head = _verify(repo, str(source.get("head_ref") or ""))
        if not base or not head:
            missing = _spec(exact=False, state_source="unavailable", diagnostic="git_ref_not_found")
            return {"before": missing, "after": missing}
        return {
            "before": _materialized(repo, base, work_dir / "before", state_source="git_ref"),
            "after": _materialized(repo, head, work_dir / "after", state_source="git_ref"),
        }
    if kind == "github_pr":
        if not origin:
            return _baseline_pair(repo, work_dir)
        base, head, diagnostic = _pull_request_refs(repo, source, settings)
        if not base or not head:
            missing = _spec(exact=False, state_source="unavailable", diagnostic=diagnostic or "git_ref_not_found")
            return {"before": missing, "after": missing}
        return {
            "before": _materialized(repo, base, work_dir / "before", state_source="git_ref"),
            "after": _materialized(repo, head, work_dir / "after", state_source="git_ref"),
        }
    if kind == "timeframe":
        return _timeframe_pair(repo, source, settings, work_dir)
    missing = _spec(exact=False, state_source="unavailable", diagnostic="invalid_source")
    return {"before": missing, "after": missing}


def _relationships_from_model(manifest: ClusterManifest, model, files_by_service: dict[str, dict[str, str]], members: set[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, str]]]:
    from dep_intel.contract_extractors.javascript import extract_javascript
    from dep_intel.contracts import _call_path, _host_index, _provider_for, match_template

    providers: list[dict[str, Any]] = []
    seen_providers: set[tuple[str, str]] = set()
    declared: set[tuple[str, str, str]] = set()
    for route in model.routes:
        if route.service_id not in members:
            continue
        if route.source == "declared":
            declared.add((route.service_id, route.method.upper(), normalize_template(route.path)))
            continue
        if route.source not in {"extracted", "openapi"}:
            continue
        identity = f"{route.method.upper()} {normalize_template(route.path)}"
        key = (route.service_id, identity)
        if key in seen_providers:
            continue
        seen_providers.add(key)
        providers.append({
            "service_id": route.service_id,
            "relationship_type": "http_contract",
            "contract_identity": identity,
            "method": route.method.upper(),
            "path": normalize_template(route.path),
            "file": _relative(route.file),
            "line": int(route.line or 0),
            "provenance": "source_extraction",
        })
    hosts = _host_index(manifest)
    raw_calls: list[dict[str, Any]] = []
    for service in manifest.services:
        if service.id not in members:
            continue
        for path, text in sorted(files_by_service.get(service.id, {}).items()):
            if PurePosixPath(path).suffix.lower() not in _JS:
                continue
            extracted = extract_javascript(path, text)
            for call in extracted.calls:
                provider, reason = _provider_for(service, call, hosts, manifest)
                if not provider or provider not in members or provider == service.id:
                    continue
                call_path, _version = _call_path(call)
                method = str(call.method or "").upper()
                if not method or not call_path:
                    continue
                raw_calls.append({
                    "service_id": service.id,
                    "provider_service": provider,
                    "method": method,
                    "path": normalize_template(call_path),
                    "file": _relative(call.file or path),
                    "line": int(call.line or 0),
                    "reason": reason,
                })
    consumers: list[dict[str, Any]] = []
    relationships: list[dict[str, Any]] = []
    notes: list[dict[str, str]] = []
    seen_calls: set[tuple[str, str, str]] = set()
    for call in raw_calls:
        identity = f"{call['method']} {call['path']}"
        key = (call["service_id"], call["provider_service"], identity)
        if key in seen_calls:
            continue
        seen_calls.add(key)
        provider_present = (call["provider_service"], identity) in seen_providers
        declared_only = not provider_present and (call["provider_service"], call["method"], call["path"]) in declared
        if declared_only:
            notes.append({
                "code": "declared_contract_not_historical",
                "service_id": call["service_id"],
                "message": f"{identity} is declared for {call['provider_service']} and was not compared as a historical boundary.",
            })
            continue
        matched = provider_present and any(
            item.service_id == call["provider_service"] and item.method.upper() == call["method"] and match_template(call["path"], item.path)[0]
            for item in model.routes
            if item.source in {"extracted", "openapi"}
        )
        consumers.append({
            "service_id": call["service_id"],
            "provider_service": call["provider_service"],
            "relationship_type": "http_contract",
            "contract_identity": identity,
            "method": call["method"],
            "path": call["path"],
            "file": call["file"],
            "line": call["line"],
            "provenance": "source_extraction",
        })
        relationships.append({
            "provider_service": call["provider_service"],
            "consumer_service": call["service_id"],
            "relationship_type": "http_contract",
            "contract_identity": identity,
            "provider_present": provider_present,
            "consumer_reference_present": True,
            "confirmed": bool(matched and provider_present),
            "provenance": "source_extraction",
            "provider_file": next((item["file"] for item in providers if item["service_id"] == call["provider_service"] and item["contract_identity"] == identity), ""),
            "provider_line": next((item["line"] for item in providers if item["service_id"] == call["provider_service"] and item["contract_identity"] == identity), 0),
            "consumer_file": call["file"],
            "consumer_line": call["line"],
        })
    relationships.sort(key=lambda item: (item["relationship_type"], item["provider_service"], item["consumer_service"], item["contract_identity"]))
    return providers, consumers, relationships, notes


def _coverage(services: dict[str, dict[str, Any]], relationships: list[dict[str, Any]], manifest: ClusterManifest, members: set[str]) -> dict[str, Any]:
    exact = [service_id for service_id, info in services.items() if info.get("exact")]
    partial = [service_id for service_id, info in services.items() if not info.get("exact")]
    kinds = sorted({item.get("relationship_type") or "unknown" for item in relationships}) or ["http_contract"]
    return {
        "services_total": len(services),
        "services_exact": len(exact),
        "services_partial": partial,
        "relationship_types": {kind: "exact" if kind == "http_contract" and not partial else "partial" for kind in kinds},
        "declared_http": _declared_count(manifest, members, "http"),
        "declared_grpc": _declared_count(manifest, members, "grpc"),
        "declared_events": _declared_count(manifest, members, "events"),
    }


def _mode_for(before: dict[str, Any], after: dict[str, Any], manifest: ClusterManifest, members: set[str]) -> tuple[str, dict[str, Any], list[str]]:
    limitations: list[str] = []
    before_exact = {service_id for service_id, info in (before.get("services") or {}).items() if info.get("exact")}
    after_exact = {service_id for service_id, info in (after.get("services") or {}).items() if info.get("exact")}
    partial = sorted(set(before.get("services") or {}) - before_exact | (set(after.get("services") or {}) - after_exact))
    declared_http = _declared_count(manifest, members, "http")
    declared_grpc = _declared_count(manifest, members, "grpc")
    declared_events = _declared_count(manifest, members, "events")
    extracted = len(before.get("relationships") or []) + len(after.get("relationships") or [])
    types = {"http_contract": "exact"}
    mode = "exact_boundary_snapshots"
    if partial:
        mode = "partial"
        types["http_contract"] = "partial"
        limitations.append("One or more repository states could not be reconstructed exactly.")
    if declared_http and extracted == 0:
        mode = "partial"
        types["http_contract"] = "declared_current"
        limitations.append("Declared HTTP contracts are not versioned in repository source, so they were not compared as historical boundaries.")
    elif declared_http:
        limitations.append("Manifest-declared HTTP contracts that are not present in source were left out of the historical comparison.")
    if declared_grpc:
        types["grpc_contract"] = "declared_current"
        limitations.append("gRPC declarations come from the current cluster manifest and were not compared historically.")
        mode = "partial"
    if declared_events:
        types["event_contract"] = "declared_current"
        limitations.append("Event declarations come from the current cluster manifest and were not compared historically.")
        mode = "partial"
    if "http_contract" not in types:
        types["http_contract"] = "exact"
    coverage = {
        "services_total": len(members),
        "services_exact_before": len(before_exact),
        "services_exact_after": len(after_exact),
        "services_partial": partial,
        "relationship_types": types,
    }
    return mode, coverage, limitations


def _declared_count(manifest: ClusterManifest, members: set[str], kind: str) -> int:
    total = 0
    for service in manifest.services:
        if service.id not in members:
            continue
        total += len(getattr(service.contracts, kind, []) or [])
    return total


def _timeframe_pair(repo: Path, source: dict[str, Any], settings: Settings, work_dir: Path) -> dict[str, dict[str, Any]]:
    from dep_intel.commit_window import window_bounds

    try:
        start, end = window_bounds(str(source.get("since") or ""), str(source.get("until") or ""))
    except ValueError:
        missing = _spec(exact=False, state_source="unavailable", diagnostic="invalid_timeframe")
        return {"before": missing, "after": missing}
    _deepen_owned(repo, settings)
    before_ref = _rev_before(repo, start.isoformat())
    after_ref = _rev_before(repo, (end + timedelta(seconds=1)).isoformat())
    if not before_ref and _shallow_gap(repo):
        missing = _spec(exact=False, state_source="history_unavailable", diagnostic="history_unavailable", ref=after_ref)
        after = _materialized(repo, after_ref, work_dir / "after", state_source="git_ref") if after_ref else dict(missing)
        return {"before": missing, "after": after}
    if not before_ref and after_ref:
        before = _empty_spec("repository_absent")
        after = _materialized(repo, after_ref, work_dir / "after", state_source="git_ref")
        return {"before": before, "after": after}
    if not before_ref and not after_ref:
        empty = _empty_spec("repository_absent")
        return {"before": empty, "after": dict(empty)}
    if not after_ref:
        after_ref = before_ref
    before = _materialized(repo, before_ref, work_dir / "before", state_source="git_ref")
    if before_ref == after_ref and before.get("exact"):
        after = _spec(root=before.get("root"), ref=after_ref, cache_key=before.get("cache_key") or after_ref, state_source="git_ref", owned=False, exact=True)
    else:
        after = _materialized(repo, after_ref, work_dir / "after", state_source="git_ref")
    return {"before": before, "after": after}


def _baseline_pair(repo: Path, work_dir: Path) -> dict[str, dict[str, Any]]:
    head = _head(repo)
    spec = _materialized(repo, head, work_dir / "baseline", state_source="git_ref") if head else _spec(exact=False, state_source="unavailable", diagnostic="git_ref_not_found")
    return {"before": spec, "after": _spec(root=spec.get("root"), ref=spec.get("ref") or "", cache_key=spec.get("cache_key") or "", state_source=spec.get("state_source") or "git_ref", owned=False, exact=bool(spec.get("exact")))}


def _pull_request_refs(repo: Path, source: dict[str, Any], settings: Settings) -> tuple[str, str, str]:
    from dep_intel.review_sources import resolve_change

    try:
        resolved = resolve_change(repo, source, settings)
    except Exception:
        log.exception("pull request resolution failed")
        return "", "", "inspection_failed"
    diagnostics = [item for item in resolved.get("diagnostics") or [] if item.get("severity") == "error"]
    base = str(resolved.get("base_sha") or "")
    head = str(resolved.get("head_sha") or "")
    if diagnostics and (not base or not head):
        return "", "", str(diagnostics[0].get("code") or "git_ref_not_found")
    return base, head, ""


def _materialized(repo: Path, sha: str, dest: Path, *, state_source: str) -> dict[str, Any]:
    from dep_intel.review_sources import materialize_worktree

    if not sha:
        return _spec(exact=False, state_source="unavailable", diagnostic="git_ref_not_found")
    if dest.exists():
        _cleanup_worktree(repo, dest)
    ok, _message = materialize_worktree(repo, sha, dest)
    if not ok:
        log.warning("worktree materialization failed for %s", sha[:12])
        return _spec(ref=sha, exact=False, state_source="unavailable", diagnostic="worktree_failed")
    return _spec(root=dest, ref=sha, cache_key=sha, state_source=state_source, owned=True, exact=True)


def _spec(**fields: Any) -> dict[str, Any]:
    spec = {
        "root": None,
        "ref": "",
        "cache_key": "",
        "state_source": "git_ref",
        "owned": False,
        "exact": False,
        "empty": False,
        "diagnostic": "",
    }
    spec.update(fields)
    return spec


def _empty_spec(state_source: str) -> dict[str, Any]:
    return _spec(ref="", cache_key=f"empty:{state_source}", state_source=state_source, exact=True, empty=True)


def _pair_status(before: dict[str, Any], after: dict[str, Any]) -> str:
    if before.get("exact") and after.get("exact"):
        return "exact"
    return "partial"


def _member_is_origin(repo: Path, source: dict[str, Any], member: dict[str, Any]) -> bool:
    selected = str(source.get("repository") or "")
    names = {str(member.get("service_id") or ""), str(member.get("repository") or ""), str(member.get("repository_path") or "")}
    names.discard("")
    if selected:
        if selected in names:
            return True
        try:
            return Path(selected).resolve() == repo.resolve()
        except OSError:
            return False
    kind = str(source.get("kind") or "")
    if kind == "commit":
        return bool(_verify(repo, str(source.get("commit") or "")))
    if kind == "range":
        return bool(_verify(repo, str(source.get("base_ref") or "")) and _verify(repo, str(source.get("head_ref") or "")))
    return False


def _deepen_owned(repo: Path, settings: Settings) -> None:
    if not _is_shallow(repo):
        return
    checkouts = settings.output_dir / "checkouts"
    try:
        if not contained(repo, checkouts):
            return
    except OSError:
        return
    _run(repo, ["fetch", "--unshallow"], timeout=120)


def _shallow_gap(repo: Path) -> bool:
    if not _is_shallow(repo):
        return False
    oldest = _run(repo, ["rev-list", "--max-parents=0", "--max-count=1", "HEAD"])[1].strip()
    if not oldest:
        return False
    code, _stdout, _stderr = _run(repo, ["rev-parse", "--verify", "--end-of-options", f"{oldest}^"])
    return code != 0


def _is_shallow(repo: Path) -> bool:
    _code, stdout, _stderr = _run(repo, ["rev-parse", "--is-shallow-repository"])
    return stdout.strip() == "true"


def _rev_before(repo: Path, stamp: str) -> str:
    _code, stdout, _stderr = _run(repo, ["rev-list", "-1", f"--before={stamp}", "HEAD"])
    return stdout.strip()


def _head(repo: Path) -> str:
    return _verify(repo, "HEAD")


def _parent(repo: Path, sha: str) -> str:
    code, stdout, _stderr = _run(repo, ["rev-parse", "--verify", "--end-of-options", f"{sha}^"])
    if code != 0:
        return ""
    return stdout.strip()


def _verify(repo: Path, ref: str) -> str:
    from dep_intel.review_sources import safe_ref

    if not ref or not safe_ref(ref):
        return ""
    code, stdout, _stderr = _run(repo, ["rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}"])
    if code != 0:
        return ""
    return stdout.strip()


def _git_ok(repo: Path) -> bool:
    code, stdout, _stderr = _run(repo, ["rev-parse", "--is-inside-work-tree"])
    return code == 0 and stdout.strip() == "true"


def _clean(repo: Path) -> bool:
    code, stdout, _stderr = _run(repo, ["status", "--porcelain"])
    return code == 0 and not stdout.strip()


def _staged_empty(repo: Path) -> bool:
    code, stdout, _stderr = _run(repo, ["diff", "--cached", "--name-only"])
    return code == 0 and not stdout.strip()


def _fingerprint(repo: Path) -> str:
    _code, stdout, _stderr = _run(repo, ["status", "--porcelain"])
    return hashlib.sha256(stdout.encode("utf-8")).hexdigest()[:12]


def _staged_fingerprint(repo: Path) -> str:
    code, stdout, _stderr = _run_bytes(repo, ["diff", "--cached", "--binary"])
    if code != 0:
        return "unstaged-unknown"
    return hashlib.sha256(stdout).hexdigest()[:12]


def _apply_staged(repo: Path, dest: Path) -> tuple[bool, str]:
    code, patch, _stderr = _run_bytes(repo, ["diff", "--cached", "--binary"])
    if code != 0:
        return False, "staged diff could not be read"
    if not patch.strip():
        return True, ""
    try:
        applied = subprocess.run(
            ["git", "-C", str(dest), "apply", "--binary", "--whitespace=nowarn"],
            input=patch,
            capture_output=True,
            timeout=60,
            check=False,
            shell=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False, "staged diff could not be applied"
    return applied.returncode == 0, ""


def _cleanup_worktree(repo: Path, dest: Path) -> None:
    from dep_intel.review_sources import remove_worktree

    if not dest.exists():
        return
    try:
        remove_worktree(repo, dest)
    except Exception:
        log.exception("worktree cleanup failed")
    if dest.exists():
        import shutil

        shutil.rmtree(dest, ignore_errors=True)


def _run(repo: Path, args: list[str], timeout: int = 30) -> tuple[int, str, str]:
    from dep_intel.review_sources import run_git

    return run_git(repo, args, timeout=timeout)


def _run_bytes(repo: Path, args: list[str], timeout: int = 60) -> tuple[int, bytes, bytes]:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            timeout=timeout,
            check=False,
            shell=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return 1, b"", b""
    return completed.returncode, completed.stdout or b"", completed.stderr or b""


def _relative(path: str) -> str:
    text = (path or "").replace("\\", "/")
    if text.startswith("/") or re_drive(text):
        return ""
    return text


def re_drive(path: str) -> bool:
    return len(path) > 2 and path[1] == ":"


def _safe(service_id: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in service_id)
    return cleaned or "service"


def _cancelled_state(state_kind: str, source_kind: str) -> dict[str, Any]:
    return {"cancelled": True, "state_kind": state_kind, "source_kind": source_kind, "services": {}, "relationships": [], "diagnostics": []}


def _canonical_blob(body: dict[str, Any]) -> str:
    import json

    return json.dumps(
        {
            "services": body.get("services"),
            "relationships": body.get("relationships"),
        },
        sort_keys=True,
        default=str,
    )


def _split_http(identity: str) -> tuple[str, str]:
    parsed = parse_contract_text(identity)
    if not parsed or " " not in parsed:
        return "", ""
    method, path = parsed.split(" ", 1)
    return method, path
