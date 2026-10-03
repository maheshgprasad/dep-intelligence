"""Bounded background jobs shared by the HTTP API and MCP tools."""

from __future__ import annotations

import threading
import traceback
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class Job:
    id: str
    name: str
    status: str = "queued"
    progress: int = 0
    error: str = ""
    result: dict[str, Any] | None = None
    cancel: threading.Event = field(default_factory=threading.Event)


class JobRunner:
    def __init__(self, workers: int, publish: Callable[[str, dict], None]) -> None:
        self._executor = ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="nami-job")
        self._publish = publish
        self._jobs: dict[str, Job] = {}
        self._futures: dict[str, Future] = {}
        self._lock = threading.Lock()

    def submit(self, name: str, fn: Callable[[Job], dict]) -> Job:
        job = Job(id=uuid.uuid4().hex, name=name)
        with self._lock:
            self._jobs[job.id] = job
            future = self._executor.submit(self._run, job, fn)
            self._futures[job.id] = future
        self._publish("job_started", {"job_id": job.id, "name": name})
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def cancel(self, job_id: str) -> Job | None:
        job = self.get(job_id)
        if job is None:
            return None
        job.cancel.set()
        future = self._futures.get(job_id)
        if future is not None:
            future.cancel()
        if job.status in {"queued", "running"}:
            job.status = "cancelled"
        return job

    def shutdown(self) -> None:
        for job in list(self._jobs.values()):
            job.cancel.set()
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _run(self, job: Job, fn: Callable[[Job], dict]) -> None:
        if job.cancel.is_set():
            job.status = "cancelled"
            return
        job.status = "running"
        self._publish("job_progress", {"job_id": job.id, "name": job.name, "percent": 1})
        try:
            result = fn(job)
        except Exception as exc:
            job.status = "failed"
            job.error = str(exc)
            job.result = {"traceback": traceback.format_exc(limit=5)}
            self._publish("job_failed", {"job_id": job.id, "name": job.name, "error": job.error})
            return
        if job.cancel.is_set():
            job.status = "cancelled"
            self._publish("job_failed", {"job_id": job.id, "name": job.name, "error": "cancelled"})
            return
        job.status = "succeeded"
        job.progress = 100
        job.result = result
        self._publish("job_completed", {"job_id": job.id, "name": job.name, "result": _compact(result)})


def _compact(result: dict) -> dict:
    if not isinstance(result, dict):
        return {"value": str(result)}
    kept = {}
    for key, value in result.items():
        if key in {"snapshot"}:
            continue
        kept[key] = value
    return kept
