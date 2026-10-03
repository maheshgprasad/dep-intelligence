"""Watch CRG databases and the manifest without rebuilding on every shm write.

A database change invalidates a partition. It is not the identity of a changed
symbol. Quiet-period stabilization is recorded as a heuristic.
"""

from __future__ import annotations

import threading
from pathlib import Path

from dep_intel.crg_adapter import database_identity
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer


class WatchCoordinator:
    def __init__(self, on_dirty, debounce_ms: int = 500, reconcile_seconds: int = 30) -> None:
        self._on_dirty = on_dirty
        self._debounce = max(0, debounce_ms) / 1000
        self._reconcile = max(1, reconcile_seconds)
        self._timers: dict[str, threading.Timer] = {}
        self._running: set[str] = set()
        self._dirty_again: set[str] = set()
        self._identities: dict[str, str] = {}
        self._lock = threading.Lock()
        self._observer: Observer | None = None
        self._reconcile_timer: threading.Timer | None = None
        self._stopped = False
        self._paths: dict[str, Path] = {}

    def start(self, watches: dict[str, Path], manifest: Path | None, repos_file: Path | None) -> None:
        self.stop()
        self._stopped = False
        self._paths = dict(watches)
        observer = Observer()
        for service_id, directory in watches.items():
            directory.mkdir(parents=True, exist_ok=True)
            observer.schedule(_DbHandler(self, service_id), str(directory), recursive=False)
            db = directory / "graph.db"
            self._identities[service_id] = database_identity(db) if db.is_file() else ""
        config_handler = _ConfigHandler(self)
        if manifest is not None and manifest.parent.is_dir():
            observer.schedule(config_handler, str(manifest.parent), recursive=False)
        if repos_file is not None and repos_file.parent.is_dir() and (manifest is None or repos_file.parent != manifest.parent):
            observer.schedule(config_handler, str(repos_file.parent), recursive=False)
        observer.start()
        self._observer = observer
        self._schedule_reconcile()

    def stop(self) -> None:
        self._stopped = True
        with self._lock:
            for timer in self._timers.values():
                timer.cancel()
            self._timers.clear()
            if self._reconcile_timer is not None:
                self._reconcile_timer.cancel()
                self._reconcile_timer = None
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=2)
            self._observer = None

    def mark(self, service_id: str, *, stabilization: str = "quiet_period") -> None:
        if self._stopped:
            return
        with self._lock:
            if service_id in self._running:
                self._dirty_again.add(service_id)
                return
            timer = self._timers.get(service_id)
            if timer is not None:
                timer.cancel()
            timer = threading.Timer(self._debounce, self._fire, args=(service_id, stabilization))
            self._timers[service_id] = timer
            timer.daemon = True
            timer.start()

    def begin(self, service_id: str) -> None:
        with self._lock:
            self._running.add(service_id)

    def finish(self, service_id: str) -> None:
        again = False
        with self._lock:
            self._running.discard(service_id)
            if service_id in self._dirty_again:
                self._dirty_again.discard(service_id)
                again = True
        if again:
            self.mark(service_id)

    def note_identity(self, service_id: str, identity: str) -> None:
        with self._lock:
            self._identities[service_id] = identity

    def reconcile(self) -> None:
        for service_id, directory in list(self._paths.items()):
            db = directory / "graph.db"
            identity = database_identity(db) if db.is_file() else ""
            with self._lock:
                previous = self._identities.get(service_id, "")
            if identity != previous:
                self.mark(service_id)
        self._schedule_reconcile()

    def _fire(self, service_id: str, stabilization: str) -> None:
        try:
            self._on_dirty(service_id, stabilization)
        except Exception:
            return

    def _schedule_reconcile(self) -> None:
        if self._stopped:
            return
        timer = threading.Timer(self._reconcile, self.reconcile)
        timer.daemon = True
        self._reconcile_timer = timer
        timer.start()


class _DbHandler(FileSystemEventHandler):
    def __init__(self, coordinator: WatchCoordinator, service_id: str) -> None:
        self._coordinator = coordinator
        self._service_id = service_id

    def on_any_event(self, event) -> None:
        name = Path(event.src_path).name
        if name == "graph.db-shm":
            return
        if name == "graph.db" or name.startswith("graph.db-wal"):
            self._coordinator.mark(self._service_id)


class _ConfigHandler(FileSystemEventHandler):
    def __init__(self, coordinator: WatchCoordinator) -> None:
        self._coordinator = coordinator

    def on_any_event(self, event) -> None:
        name = Path(event.src_path).name
        if name in {"cluster-manifest.json", "repos.txt"}:
            self._coordinator.mark("__config__")
