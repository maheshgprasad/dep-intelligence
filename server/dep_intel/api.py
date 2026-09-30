"""HTTP API for the Carbon dashboard, including a Server-Sent Events stream."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from dep_intel.config import Settings
from dep_intel.pipeline import run_all, run_graphs
from dep_intel.store import read_json

app = FastAPI(title="dep-intel")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_subscribers: set[asyncio.Queue[str]] = set()
_running = False


def publish(event: str, data: dict) -> None:
    payload = f"event: {event}\ndata: {json.dumps(data)}\n\n"
    for queue in list(_subscribers):
        try:
            queue.put_nowait(payload)
        except asyncio.QueueFull:
            continue


def _snapshot(settings: Settings) -> dict:
    output = settings.output_dir
    graphs = read_json(output, "repo_graphs.json") or {"repos": []}
    details = {}
    for repo in graphs.get("repos") or []:
        slug = repo["slug"]
        details[slug] = {
            "graph": read_json(output / "graphs" / slug, "graph.json"),
            "cochange": read_json(output / "graphs" / slug, "cochange.json"),
        }
    report_md = output / "security_release_report.md"
    return {
        "languages": read_json(output, "language_detection.json"),
        "matrix": read_json(output, "dep_matrix.json"),
        "updates": read_json(output, "package_updates.json"),
        "review": read_json(output, "code_review.json"),
        "coverage": read_json(output, "test_coverage.json"),
        "apis": read_json(output, "api_dependencies.json"),
        "vulnerabilities": read_json(output, "vulnerabilities.json"),
        "cves": read_json(output, "cve_analysis.json"),
        "security": read_json(output, "security_release_report.json"),
        "security_markdown": report_md.read_text(encoding="utf-8") if report_md.is_file() else "",
        "graphs": graphs,
        "graph_details": details,
        "running": _running,
    }


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.get("/api/snapshot")
def snapshot() -> dict:
    return _snapshot(Settings.load())


@app.get("/api/events")
async def events() -> StreamingResponse:
    queue: asyncio.Queue[str] = asyncio.Queue(maxsize=50)
    _subscribers.add(queue)

    async def stream() -> AsyncIterator[str]:
        try:
            yield "event: hello\ndata: {}\n\n"
            while True:
                try:
                    yield await asyncio.wait_for(queue.get(), timeout=15)
                except TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            _subscribers.discard(queue)

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.post("/api/analysis/run")
async def start_analysis() -> dict:
    global _running
    if _running:
        return {"started": False, "message": "Analysis is already running."}
    _running = True
    loop = asyncio.get_running_loop()

    def on_progress(phase: str, percent: int, message: str) -> None:
        loop.call_soon_threadsafe(publish, "progress", {"phase": phase, "percent": percent, "message": message})

    async def job() -> None:
        global _running
        try:
            await asyncio.to_thread(run_all, "", "", on_progress)
            publish("file_updated", {"filename": "snapshot"})
        finally:
            _running = False

    asyncio.create_task(job())
    return {"started": True}


@app.post("/api/graphs/build")
async def start_graphs() -> dict:
    global _running
    if _running:
        return {"started": False, "message": "Analysis is already running."}
    _running = True
    loop = asyncio.get_running_loop()

    def on_progress(phase: str, percent: int, message: str) -> None:
        loop.call_soon_threadsafe(publish, "progress", {"phase": phase, "percent": percent, "message": message})

    async def job() -> None:
        global _running
        try:
            await asyncio.to_thread(run_graphs, "", "", 365, on_progress)
            publish("file_updated", {"filename": "repo_graphs.json"})
        finally:
            _running = False

    asyncio.create_task(job())
    return {"started": True}


def main() -> None:
    import uvicorn

    port = int(os.environ.get("API_PORT", "8010"))
    uvicorn.run("dep_intel.api:app", host="127.0.0.1", port=port)
