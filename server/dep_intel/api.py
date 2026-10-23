"""HTTP API for the Carbon dashboard, including a Server-Sent Events stream."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from dep_intel.cluster_service import get_engine, shutdown_engine
from dep_intel.config import PROJECT_ROOT, Settings
from dep_intel.crg import call_tool, crg_diagnostics
from dep_intel.crg_adapter import resolve_graph_db
from dep_intel.events import bus, format_sse
from dep_intel.graph import checkout_for, refresh_graphs
from dep_intel.graph_labels import annotate_flow_paths, load_node_labels
from dep_intel.paths import contained
from dep_intel.pipeline import run_all, run_graphs
from dep_intel.store import read_json

DIST = PROJECT_ROOT / "web" / "dist"


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    loop = asyncio.get_running_loop()
    bus.bind(loop)
    engine = get_engine(Settings.load())
    tasks: set[asyncio.Task] = set()

    async def warm() -> None:
        try:
            await asyncio.to_thread(refresh_graphs, Settings.load())
            publish("file_updated", {"filename": "crg"})
        except Exception:
            return

    def _track(task: asyncio.Task) -> None:
        tasks.add(task)
        task.add_done_callback(tasks.discard)

        def _log(done: asyncio.Task) -> None:
            if done.cancelled():
                return
            exc = done.exception()
            if exc is not None:
                publish("job_failed", {"name": "startup", "error": str(exc)})

        task.add_done_callback(_log)

    # Startup serves the last published snapshot. Rebuilding CRG graphs or
    # watching graph.db is on-demand (Refresh graph / Run analysis), not boot.
    if os.environ.get("NAMI_EAGER_GRAPH_REFRESH", "0") == "1":
        _track(asyncio.create_task(warm()))
    if os.environ.get("NAMI_CLUSTER_WATCH", "0") == "1":
        _track(asyncio.create_task(asyncio.to_thread(engine.start_watcher)))
    try:
        yield
    finally:
        for task in list(tasks):
            task.cancel()
        await asyncio.to_thread(shutdown_engine)


app = FastAPI(title="Nami Trace", lifespan=_lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_running = False
_background: set[asyncio.Task] = set()


def publish(event: str, data: dict) -> None:
    bus.publish(event, data)


def _spawn(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _background.add(task)

    def _done(done: asyncio.Task) -> None:
        _background.discard(done)
        if done.cancelled():
            return
        exc = done.exception()
        if exc is not None:
            bus.publish("job_failed", {"name": "background", "error": str(exc)})

    task.add_done_callback(_done)
    return task


def _snapshot(settings: Settings) -> dict:
    output = settings.output_dir
    graphs = read_json(output, "repo_graphs.json") or {"repos": []}
    details = {}
    for repo in graphs.get("repos") or []:
        slug = repo["slug"]
        crg = read_json(output / "graphs" / slug, "crg.json")
        _label_graph_paths(crg, repo)
        details[slug] = {
            "crg": crg,
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
        "analysis_status": read_json(output, "analysis_status.json"),
        "cluster": get_engine(settings).summary(),
        "running": _running,
    }


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "crg": crg_diagnostics()}


@app.get("/api/snapshot")
def snapshot() -> dict:
    return _snapshot(Settings.load())


@app.get("/api/events")
async def events(request: Request) -> StreamingResponse:
    ident, queue = bus.subscribe()
    last_header = request.headers.get("last-event-id")
    replay, missing = bus.replay_after(int(last_header) if last_header and last_header.isdigit() else None)

    async def stream() -> AsyncIterator[str]:
        try:
            if missing:
                yield format_sse({"id": 0, "event": "resync", "data": {"reason": "history_unavailable"}})
            else:
                for record in replay:
                    yield format_sse(record)
            yield format_sse({"id": replay[-1]["id"] if replay else 0, "event": "hello", "data": {}})
            while True:
                try:
                    yield await asyncio.wait_for(queue.get(), timeout=15)
                except TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            bus.unsubscribe(ident)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


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

    _spawn(job())
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

    _spawn(job())
    return {"started": True}


@app.post("/api/intelligence/refresh")
async def refresh_intelligence() -> dict:
    """Refresh graphs, commit history, and the cluster contract model.

    Package updates, OSV, coverage, Mend issues, and the legacy static review stay out of this path.
    """
    global _running
    if _running:
        return {"started": False, "message": "A refresh is already running."}
    _running = True
    loop = asyncio.get_running_loop()

    def on_progress(phase: str, percent: int, message: str) -> None:
        loop.call_soon_threadsafe(publish, "progress", {"phase": phase, "percent": percent, "message": message})

    async def job() -> None:
        global _running
        try:
            on_progress("repository_graph", 15, "Refreshing intelligence")
            await asyncio.to_thread(run_graphs, "", "", 365, on_progress)
            on_progress("cluster_graph", 90, "Refreshing contracts")
            await asyncio.to_thread(get_engine(Settings.load()).refresh)
            publish("file_updated", {"filename": "snapshot"})
            on_progress("complete", 100, "Intelligence refresh finished")
        finally:
            _running = False

    _spawn(job())
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
        payload = await asyncio.to_thread(call_tool, body.tool, root, body.arguments)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    _label_graph_paths(payload, {"slug": body.slug, "checkout": root})
    return payload


def _label_graph_paths(payload: dict | None, repo: dict) -> None:
    checkout = str(repo.get("checkout") or "")
    if not payload or not checkout:
        return
    try:
        database = resolve_graph_db(
            Path(checkout),
            ".code-review-graph/graph.db",
            output_dir=Settings.load().output_dir,
            slug=str(repo.get("slug") or ""),
        )
        labels = load_node_labels(database, checkout)
    except Exception:
        return
    if labels:
        annotate_flow_paths(payload, labels, checkout)


def resolve_ui_file(dist: Path, full_path: str) -> Path | None:
    if not full_path:
        return None
    candidate = (dist / full_path).resolve()
    if contained(candidate, dist) and candidate.is_file():
        return candidate
    return None


def _ui(full_path: str) -> FileResponse:
    index = DIST / "index.html"
    if not index.is_file():
        raise HTTPException(status_code=503, detail="The dashboard build is missing. Run scripts/nami-trace.sh.")
    candidate = resolve_ui_file(DIST, full_path)
    if candidate is not None:
        return FileResponse(candidate)
    return FileResponse(index)


class ImpactBody(BaseModel):
    service: str = ""
    symbol: str = ""
    file: str = ""
    line: int | None = None
    contract_id: str = ""
    mode: str = "code_change"
    scenario: str = ""
    topology: str = "current"
    graph_version: str = ""
    budget: dict[str, Any] = Field(default_factory=dict)


class HybridImpactBody(ImpactBody):
    as_of: str | None = None
    temporal_provider: str = "commit_history"
    weights: dict[str, float] = Field(default_factory=dict)
    cross_repo_window_days: int = 7
    temporal_candidate_limit: int = 25
    expand_temporal: bool = True
    experiment: str = "hybrid"
    fusion_method: str = "raw_weighted"
    path_scoring: str = "coverage_adjusted"
    lag_matching: str = "one_to_one"
    temporal: dict[str, Any] = Field(default_factory=dict)


@app.post("/api/cluster/refresh")
async def cluster_refresh() -> dict:
    engine = get_engine(Settings.load())
    return await asyncio.to_thread(engine.refresh_async)


@app.get("/api/cluster/graph")
async def cluster_graph(service: str = "", kind: str = "", q: str = "", limit: int = 100, offset: int = 0) -> dict:
    engine = get_engine(Settings.load())
    summary = await asyncio.to_thread(engine.summary)
    page = await asyncio.to_thread(engine.slice, service=service, kind=kind, query=q, limit=limit, offset=offset)
    return {"summary": summary, "page": page}


@app.get("/api/cluster/contracts")
async def cluster_contracts() -> dict:
    return await asyncio.to_thread(get_engine(Settings.load()).contracts)


@app.post("/api/cluster/impact")
async def cluster_impact(body: ImpactBody) -> dict:
    status, payload = await asyncio.to_thread(get_engine(Settings.load()).impact, body.model_dump())
    if status != 200:
        raise HTTPException(status_code=status, detail=payload)
    return payload


@app.get("/api/cluster/nrm/capabilities")
def nrm_capabilities() -> dict:
    from dep_intel.hybrid_impact import NRM_VERSION
    from pathlib import Path

    phase1 = Path("output/phase1_predictions.json")
    settings = Settings.load()
    artifact = settings.output_dir / "phase1_predictions.json"
    return {
        "module_version": NRM_VERSION,
        "ranking_unit": "file",
        "fusion_methods": ["raw_weighted", "rank_normalized"],
        "temporal_providers": [
            {"id": "commit_history", "available": True, "quality": "complete"},
            {
                "id": "phase1",
                "available": artifact.is_file() or phase1.is_file(),
                "reason": "" if artifact.is_file() else "output/phase1_predictions.json not found",
            },
        ],
        "lag_matching": ["one_to_one", "reusable_target"],
        "defaults": {"structural_weight": 0.7, "temporal_weight": 0.3, "cross_repo_window_days": 7},
    }


@app.post("/api/cluster/hybrid-impact")
async def cluster_hybrid_impact(body: HybridImpactBody) -> dict:
    status, payload = await asyncio.to_thread(get_engine(Settings.load()).hybrid_impact, body.model_dump())
    if status != 200:
        raise HTTPException(status_code=status, detail=payload)
    return payload


class ReviewSourceBody(BaseModel):
    kind: str = "working_tree"
    repository: str = ""
    service_id: str | None = None
    base_ref: str | None = None
    head_ref: str | None = None
    commit: str | None = None
    pr_url: str | None = None
    pr_number: int | None = None
    requested_by: str | None = None
    files: list[dict[str, Any]] = Field(default_factory=list)


class ReviewRunBody(BaseModel):
    source: ReviewSourceBody
    mode: str = "fast"
    verify: bool = True
    model: dict[str, Any] = Field(default_factory=dict)
    service: str = ""


class ReviewFeedbackBody(BaseModel):
    finding_fingerprint: str
    status: str
    scope: dict[str, Any] = Field(default_factory=dict)
    comment: str = ""


def _review_engine():
    from dep_intel.review_engine import ReviewEngine

    settings = Settings.load()
    return ReviewEngine(settings, cluster=get_engine(settings), publish=publish)


@app.post("/api/review/change-view")
async def review_change_view(body: ReviewRunBody) -> dict:
    engine = _review_engine()
    report = await asyncio.to_thread(engine.run, {**body.model_dump(), "verify": False, "mode": "fast"})
    return {"change": report.get("change"), "changed_files": report.get("changed_files"), "changed_symbols": report.get("changed_symbols"), "contract_changes": report.get("contract_changes"), "diagnostics": report.get("diagnostics"), "review_id": report.get("review_id")}


@app.post("/api/review/run")
async def review_run(body: ReviewRunBody) -> dict:
    engine = _review_engine()
    payload = body.model_dump()
    if payload.get("mode") == "exact":
        cluster = get_engine(Settings.load())

        def _job(active) -> dict:
            publish("review_started", {"job_id": active.id, "mode": "exact"})
            return engine.run(payload, cancel=active.cancel)

        job = cluster.submit("boundary_review", _job)
        return {"job_id": job.id, "status": job.status, "name": job.name}
    return await asyncio.to_thread(engine.run, payload)


@app.get("/api/review/config")
def review_config(repository: str = "") -> dict:
    from dep_intel.review_engine import public_config

    settings = Settings.load()
    repo = (settings.root / repository) if repository else settings.root
    return public_config(repo, settings)


@app.get("/api/review/{review_id}")
def review_get(review_id: str) -> dict:
    from dep_intel.review_store import read_review

    stored = read_review(Settings.load().output_dir, review_id)
    if stored is None:
        raise HTTPException(status_code=404, detail={"code": "review_not_found"})
    return stored


@app.get("/api/review/{review_id}/report")
def review_report(review_id: str) -> FileResponse:
    path = Settings.load().output_dir / "reviews" / review_id / "report.md"
    if not path.is_file():
        raise HTTPException(status_code=404, detail={"code": "review_not_found"})
    return FileResponse(path, media_type="text/markdown")


@app.get("/api/review/{review_id}/finding/{finding_id}")
def review_finding(review_id: str, finding_id: str) -> dict:
    payload = _review_engine().explain(review_id, finding_id)
    if payload.get("code"):
        raise HTTPException(status_code=404, detail=payload)
    return payload


@app.post("/api/review/{review_id}/feedback")
def review_feedback(review_id: str, body: ReviewFeedbackBody) -> dict:
    status, payload = _review_engine().feedback(review_id, body.model_dump())
    if status != 200:
        raise HTTPException(status_code=status, detail=payload)
    return payload


@app.get("/api/jobs/{job_id}")
def cluster_job(job_id: str) -> dict:
    job = get_engine(Settings.load()).job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown job")
    return {"id": job.id, "name": job.name, "status": job.status, "progress": job.progress, "error": job.error, "result": job.result}


@app.post("/api/jobs/{job_id}/cancel")
def cluster_job_cancel(job_id: str) -> dict:
    job = get_engine(Settings.load()).cancel_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown job")
    return {"id": job.id, "status": job.status}


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
