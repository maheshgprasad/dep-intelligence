"""HTTP API for the Carbon dashboard, including a Server-Sent Events stream."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from dep_intel.config import PROJECT_ROOT, Settings
from dep_intel.crg import call_tool
from dep_intel.graph import checkout_for, refresh_graphs
from dep_intel.pipeline import run_all, run_graphs
from dep_intel.store import read_json

DIST = PROJECT_ROOT / "web" / "dist"


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    async def warm() -> None:
        try:
            await asyncio.to_thread(refresh_graphs, Settings.load())
            publish("file_updated", {"filename": "crg"})
        except Exception:
            return

    task = asyncio.create_task(warm())
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(title="Nami Trace", lifespan=_lifespan)
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
            "crg": read_json(output / "graphs" / slug, "crg.json"),
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


class GraphQuery(BaseModel):
    slug: str
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)


@app.post("/api/graphs/query")
async def graph_query(body: GraphQuery) -> dict:
    settings = Settings.load()
    try:
        root = checkout_for(settings, body.slug)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="That repository is not in repos.txt.") from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        return await asyncio.to_thread(call_tool, body.tool, root, body.arguments)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def _ui(full_path: str) -> FileResponse:
    index = DIST / "index.html"
    if not index.is_file():
        raise HTTPException(status_code=503, detail="The dashboard build is missing. Run scripts/nami-trace.sh.")
    if full_path:
        candidate = (DIST / full_path).resolve()
        if str(candidate).startswith(str(DIST.resolve())) and candidate.is_file():
            return FileResponse(candidate)
    return FileResponse(index)


@app.get("/")
def dashboard() -> FileResponse:
    return _ui("")


@app.get("/{full_path:path}")
def dashboard_files(full_path: str) -> FileResponse:
    if full_path == "api" or full_path.startswith("api/"):
        raise HTTPException(status_code=404)
    return _ui(full_path)


def main() -> None:
    import uvicorn

    port = int(os.environ.get("PORT") or os.environ.get("API_PORT") or "3002")
    uvicorn.run("dep_intel.api:app", host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
