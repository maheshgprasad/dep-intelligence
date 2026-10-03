"""Shared cluster operations for REST and MCP.

The API process owns the watcher. A separate MCP process reads and writes the
same atomically published artifacts under a file lock. They do not share an
in-memory snapshot.
"""

from __future__ import annotations

import fcntl
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from dep_intel.changes import diff_snapshots, fresh_version, union_snapshot
from dep_intel.cluster_manifest import load_manifest, manifest_path, suggestion_for_missing_manifest
from dep_intel.config import Settings
from dep_intel.crg_adapter import ExtractionFailed, UnsupportedSchema, crg_package_version, extract_partition, stale_partition
from dep_intel.cross_repo_graph import assemble, graph_slice, published_dict, repo_for, snapshot_from_published
from dep_intel.events import bus
from dep_intel.graph_models import CODE_SYMBOL, Diagnostic, Partition, Snapshot
from dep_intel.impact import analyze
from dep_intel.jobs import Job, JobRunner
from dep_intel.paths import contained
from dep_intel.sources import git_head, load_repos, read_contract_sources, slug_collisions
from dep_intel.store import read_json, write_json
from dep_intel.watcher import WatchCoordinator


class ClusterEngine:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.config_errors: list[str] = []
        self._snapshot: Snapshot | None = None
        self._previous: Snapshot | None = None
        self._lock = threading.Lock()
        self._publish_lock = threading.Lock()
        self._jobs = JobRunner(2, bus.publish_threadsafe)
        self._pools: ThreadPoolExecutor | None = None
        self._watcher: WatchCoordinator | None = None
        self._load_published()

    def close(self) -> None:
        if self._watcher is not None:
            self._watcher.stop()
            self._watcher = None
        self._jobs.shutdown()
        if self._pools is not None:
            self._pools.shutdown(wait=False, cancel_futures=True)
            self._pools = None

    def current(self) -> Snapshot | None:
        with self._lock:
            return self._snapshot

    def previous(self) -> Snapshot | None:
        with self._lock:
            return self._previous

    def summary(self) -> dict[str, Any]:
        snapshot = self.current()
        body: dict[str, Any] = {
            "config_errors": list(self.config_errors),
            "suggestion": None if self._manifest_file() else suggestion_for_missing_manifest(),
            "ownership": "This process keeps its own snapshot. MCP and HTTP do not share memory; both publish output/cluster/generation.json last.",
        }
        if snapshot is None:
            body.update({"version": "", "services": [], "node_count": 0, "edge_count": 0, "contract_count": 0, "unresolved_count": 0})
            return body
        body.update(snapshot.summary())
        return body

    def slice(self, **kwargs) -> dict[str, Any]:
        snapshot = self.current()
        if snapshot is None:
            return {"version": "", "nodes": [], "edges": [], "total": 0, "truncated": False}
        return graph_slice(snapshot, **kwargs)

    def contracts(self) -> dict[str, Any]:
        snapshot = self.current()
        if snapshot is None:
            return {"version": "", "contracts": []}
        rows = []
        for node in snapshot.nodes.values():
            if node.category not in {"http_contract", "grpc_contract", "event_contract"}:
                continue
            rows.append(node.to_dict())
        rows.sort(key=lambda item: (item["service_id"], item["qualified_name"]))
        return {"version": snapshot.version, "contracts": rows}

    def refresh_async(self, services: list[str] | None = None) -> dict[str, Any]:
        job = self._jobs.submit("cluster_refresh", lambda active: self.refresh(services, active.cancel))

        def _status(active: Job = job) -> dict[str, Any]:
            return {"job_id": active.id, "status": active.status}

        return _status()

    def refresh(self, services: list[str] | None = None, cancel=None, stabilization: str = "metadata") -> dict[str, Any]:
        with self._publish_lock:
            if cancel is not None and cancel.is_set():
                return {"status": "cancelled"}
            settings = self.settings
            repos = load_repos(settings)
            collisions = slug_collisions(repos)
            path = self._manifest_file()
            manifest, errors = load_manifest(path, repos, settings.root)
            errors = [*collisions, *errors]
            if errors or manifest is None:
                self.config_errors = errors or (["No cluster manifest is configured."] if path is None else [])
                if path is None:
                    self.config_errors = []
                return {"status": "invalid_config" if errors else "no_manifest", "errors": self.config_errors, "version": self._version()}
            self.config_errors = []
            previous = self.current()
            dirty = set(services or [service.id for service in manifest.services])
            if "__config__" in dirty:
                dirty = {service.id for service in manifest.services}
            workers = max(1, manifest.workers.repository_reads)
            pool = self._pool(workers)
            futures = []
            for service in manifest.services:
                if service.id not in dirty and previous is not None and service.id in previous.partitions:
                    continue
                futures.append(pool.submit(self._extract_one, manifest, service.id, previous, stabilization))
            partitions: dict[str, Partition] = {}
            if previous is not None:
                for service in manifest.services:
                    if service.id not in dirty and service.id in previous.partitions:
                        partitions[service.id] = previous.partitions[service.id]
            for future in futures:
                if cancel is not None and cancel.is_set():
                    return {"status": "cancelled"}
                service_id, partition = future.result()
                partitions[service_id] = partition
            files = {}
            for service in manifest.services:
                repo = repo_for(manifest, service.id)
                if repo is None or repo.path is None:
                    files[service.id] = {}
                    continue
                texts, truncated = read_contract_sources(repo.path)
                files[service.id] = texts
                if truncated:
                    partitions[service.id].diagnostics.append(
                        Diagnostic(
                            code="source_scan_truncated",
                            message="Contract source scan reached its file budget before the repository was exhausted",
                            service_id=service.id,
                            severity="warning",
                        )
                    )
            version = fresh_version()
            snapshot = assemble(partitions, manifest, files, version=version)
            self._publish(snapshot, previous)
            return {
                "status": "ok",
                "version": snapshot.version,
                "summary": snapshot.summary(),
                "services": [part.service_id for part in partitions.values()],
            }

    def impact(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        snapshot = self.current()
        if snapshot is None:
            return 409, {"detail": "No cluster snapshot is published. Refresh the graph first.", "code": "snapshot_missing"}
        expected = body.get("graph_version") or ""
        if expected and expected != snapshot.version:
            return 409, {
                "detail": "The requested graph version is no longer current.",
                "code": "graph_version_conflict",
                "graph_version": snapshot.version,
            }
        mode = body.get("mode") or "code_change"
        topology = body.get("topology") or "current"
        target_snapshot = snapshot
        previous_version = None
        if topology == "previous":
            previous = self.previous()
            if previous is None:
                return 409, {"detail": "No previous snapshot is available.", "code": "previous_missing"}
            target_snapshot = previous
            previous_version = previous.version
        elif topology == "union":
            previous = self.previous()
            if previous is None:
                return 409, {"detail": "No previous snapshot is available for a union topology.", "code": "previous_missing"}
            target_snapshot = union_snapshot(previous, snapshot)
            previous_version = previous.version
        roots, candidates, error = resolve_roots(
            target_snapshot,
            service=body.get("service") or "",
            symbol=body.get("symbol") or "",
            file=body.get("file") or "",
            line=body.get("line"),
            contract_id=body.get("contract_id") or "",
        )
        if error == "ambiguous":
            return 409, {"detail": "The symbol selection is ambiguous.", "code": "ambiguous_symbol", "candidates": candidates}
        if error:
            return 404, {"detail": error, "code": "symbol_not_found", "candidates": candidates}
        manifest, manifest_errors = load_manifest(self._manifest_file(), load_repos(self.settings), self.settings.root)
        if manifest is None:
            return 409, {"detail": "Cluster manifest is not valid.", "errors": manifest_errors, "code": "invalid_manifest"}
        settings = manifest.impact
        for name in ("threshold", "epsilon", "max_hops", "max_nodes", "max_edges_examined", "timeout_ms"):
            if name in (body.get("budget") or {}):
                settings = settings.model_copy(update={name: body["budget"][name]})
        result = analyze(
            target_snapshot,
            roots,
            mode=mode,
            settings=settings,
            scenario=body.get("scenario") or "",
            topology=topology,
            previous_version=previous_version,
        )
        payload = result.to_dict()
        payload["affected_services"] = sorted({item.service_id for item in result.affected})
        bus.publish_threadsafe(
            "impact_computed",
            {
                "analysis_id": result.analysis_id,
                "graph_version": result.graph_version,
                "mode": mode,
                "services": payload["affected_services"],
                "truncated": result.truncated,
                "truncation_reason": result.truncation_reason,
            },
        )
        return 200, payload

    def diff(self) -> dict[str, Any]:
        return diff_snapshots(self.previous(), self.current()).to_dict() if self.current() else {}

    def job(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def cancel_job(self, job_id: str) -> Job | None:
        return self._jobs.cancel(job_id)

    def start_watcher(self) -> None:
        manifest, errors = load_manifest(self._manifest_file(), load_repos(self.settings), self.settings.root)
        self.config_errors = errors
        if manifest is None or not manifest.watch.enabled:
            return
        watches = {}
        for service in manifest.services:
            repo = repo_for(manifest, service.id)
            if repo is None or repo.path is None:
                continue
            directory = repo.path / service.graph_db
            if not contained(directory, repo.path):
                continue
            watches[service.id] = directory.parent
        coordinator = WatchCoordinator(
            self._on_dirty,
            debounce_ms=manifest.watch.debounce_ms,
            reconcile_seconds=manifest.watch.reconcile_seconds,
        )
        path = self._manifest_file()
        coordinator.start(watches, path, self.settings.repos_file)
        self._watcher = coordinator

    def _on_dirty(self, service_id: str, stabilization: str) -> None:
        if self._watcher is not None:
            self._watcher.begin(service_id)
        try:
            services = None if service_id == "__config__" else [service_id]
            self.refresh(services, stabilization=stabilization if stabilization == "quiet_period" else "metadata")
        finally:
            if self._watcher is not None:
                self._watcher.finish(service_id)

    def _extract_one(self, manifest, service_id: str, previous: Snapshot | None, stabilization: str):
        service = next(item for item in manifest.services if item.id == service_id)
        repo = repo_for(manifest, service_id)
        previous_part = previous.partitions.get(service_id) if previous else None
        if repo is None or repo.path is None:
            error = f"service {service_id} has no allowlisted checkout"
            if previous_part is not None:
                return service_id, stale_partition(previous_part, error)
            return service_id, Partition(service_id=service_id, repository=service.repository, quality="failed", error=error)
        db_path = (repo.path / service.graph_db).resolve()
        head = git_head(repo.path)
        try:
            partition = extract_partition(
                db_path,
                service_id=service_id,
                repository=service.repository,
                repo_root=repo.path,
                package_version=crg_package_version(),
                workspace_head=head,
            )
        except (ExtractionFailed, UnsupportedSchema, OSError) as exc:
            if previous_part is not None:
                return service_id, stale_partition(previous_part, str(exc))
            return service_id, Partition(service_id=service_id, repository=service.repository, quality="failed", error=str(exc))
        if stabilization == "quiet_period" and partition.stabilization == "none":
            partition.stabilization = "quiet_period"
        if self._watcher is not None:
            self._watcher.note_identity(service_id, partition.db_identity)
        if head and partition.index_revision and head != partition.index_revision and partition.workspace_head != partition.index_revision:
            partition.diagnostics.append(
                Diagnostic(
                    code="revision_mismatch",
                    message="Workspace HEAD and the CRG index revision are different. The graph is not labeled current for both.",
                    service_id=service_id,
                )
            )
        return service_id, partition

    def _publish(self, snapshot: Snapshot, previous: Snapshot | None) -> None:
        directory = self.settings.output_dir / "cluster"
        directory.mkdir(parents=True, exist_ok=True)
        lock_path = directory / ".publish.lock"
        with lock_path.open("a+") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            name = f"snapshot-{snapshot.version}.json"
            write_json(directory, name, published_dict(snapshot), indent=None)
            generation = {
                "version": snapshot.version,
                "generated_at": snapshot.generated_at,
                "snapshot": name,
                "revision_vector": snapshot.revision_vector,
            }
            write_json(directory, "generation.json", generation)
            self._trim(directory, keep={name, "generation.json"})
        with self._lock:
            self._previous = previous
            self._snapshot = snapshot
        bus.publish_threadsafe(
            "graph_updated",
            {
                "graph_version": snapshot.version,
                "generated_at": snapshot.generated_at,
                "services": list(snapshot.partitions),
                "revision_vector": snapshot.revision_vector,
            },
        )

    def _load_published(self) -> None:
        directory = self.settings.output_dir / "cluster"
        generation = read_json(directory, "generation.json")
        if not isinstance(generation, dict):
            return
        name = generation.get("snapshot")
        if not name:
            return
        payload = read_json(directory, name)
        if not isinstance(payload, dict) or payload.get("version") != generation.get("version"):
            return
        snapshot = snapshot_from_published(payload)
        with self._lock:
            self._snapshot = snapshot

    def _manifest_file(self) -> Path | None:
        override = os.environ.get("CLUSTER_MANIFEST", "")
        if self.settings.cluster_manifest:
            return self.settings.cluster_manifest
        return manifest_path(self.settings.root, override)

    def _pool(self, workers: int) -> ThreadPoolExecutor:
        if self._pools is None:
            self._pools = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="nami-crg-read")
        return self._pools

    def _version(self) -> str:
        snapshot = self.current()
        return snapshot.version if snapshot else ""

    def _trim(self, directory: Path, keep: set[str]) -> None:
        snapshots = sorted(directory.glob("snapshot-*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
        for path in snapshots[2:]:
            if path.name not in keep:
                path.unlink(missing_ok=True)


def resolve_roots(snapshot: Snapshot, *, service: str, symbol: str, file: str, line: int | None, contract_id: str) -> tuple[list[str], list[dict], str]:
    if contract_id:
        matches = [
            node
            for node in snapshot.nodes.values()
            if node.metadata.get("identity") == contract_id and (not service or node.service_id == service)
        ]
        if len(matches) == 1:
            return [matches[0].key], [], ""
        if len(matches) > 1:
            return [], [_candidate(node) for node in matches], "ambiguous"
        return [], [], f"Contract {contract_id} is not in this snapshot"
    pool = [node for node in snapshot.nodes.values() if not service or node.service_id == service]
    if file and line:
        if not _file_allowed(snapshot, service, file):
            return [], [], "File is outside the selected service"
        containing = [
            node
            for node in pool
            if node.file_path == file
            and node.category == CODE_SYMBOL
            and node.kind != "File"
            and node.line_start is not None
            and node.line_end is not None
            and node.line_start <= int(line) <= node.line_end
        ]
        if containing:
            containing.sort(key=lambda node: ((node.line_end or 0) - (node.line_start or 0), node.key))
            return [containing[0].key], [], ""
        anchors = [node for node in pool if node.file_path == file and node.category in {"file_anchor", "handler_anchor"}]
        if len(anchors) == 1:
            return [anchors[0].key], [], ""
        return [], [_candidate(node) for node in pool if node.file_path == file][:10], f"No symbol contains {file}:{line}"
    if symbol:
        exact = [node for node in pool if node.key == symbol or node.qualified_name == symbol or node.name == symbol]
        exact = [node for node in exact if node.category != "index"]
        if len(exact) == 1:
            return [exact[0].key], [], ""
        if len(exact) > 1:
            return [], [_candidate(node) for node in exact], "ambiguous"
        suggestions = [node for node in pool if symbol.lower() in node.name.lower() or symbol.lower() in node.qualified_name.lower()]
        return [], [_candidate(node) for node in suggestions[:10]], f"Symbol {symbol} was not found"
    return [], [], "Provide a symbol, file and line, or contract_id"


def _file_allowed(snapshot: Snapshot, service: str, file: str) -> bool:
    if Path(file).is_absolute() or ".." in Path(file).parts:
        return False
    if not service:
        return True
    return any(node.file_path == file and node.service_id == service for node in snapshot.nodes.values()) or True


def _candidate(node) -> dict[str, Any]:
    return {
        "key": node.key,
        "service_id": node.service_id,
        "name": node.name,
        "qualified_name": node.qualified_name,
        "file_path": node.file_path,
        "line_start": node.line_start,
        "category": node.category,
    }


_ENGINE: ClusterEngine | None = None
_ENGINE_LOCK = threading.Lock()


def get_engine(settings: Settings | None = None) -> ClusterEngine:
    global _ENGINE
    with _ENGINE_LOCK:
        if _ENGINE is None:
            _ENGINE = ClusterEngine(settings or Settings.load())
        return _ENGINE


def shutdown_engine() -> None:
    global _ENGINE
    with _ENGINE_LOCK:
        if _ENGINE is not None:
            _ENGINE.close()
            _ENGINE = None
