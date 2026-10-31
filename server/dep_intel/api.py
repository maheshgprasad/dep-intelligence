"""HTTP API for the Carbon dashboard, including a Server-Sent Events stream."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
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
    """Compatibility snapshot. The React app uses /api/app-state instead."""
    return _snapshot(Settings.load())


@app.get("/api/app-state")
def app_state() -> dict:
    from dep_intel.app_state import build_app_state

    settings = Settings.load()
    return build_app_state(settings, get_engine(settings).summary(), _running)


@app.get("/api/repositories/{slug}/architecture")
def repository_architecture(slug: str) -> dict:
    from dep_intel.app_state import repository_architecture as load_architecture

    payload = load_architecture(Settings.load(), slug)
    if payload is None:
        raise HTTPException(status_code=404, detail={"code": "unknown_repository"})
    return payload


@app.get("/api/repository-health/packages")
def repository_health_packages() -> dict:
    from dep_intel.app_state import health_packages

    return health_packages(Settings.load())


@app.get("/api/repository-health/security")
def repository_health_security() -> dict:
    from dep_intel.app_state import health_security

    return health_security(Settings.load())


@app.get("/api/repository-health/security/report")
def repository_health_security_report() -> FileResponse:
    from dep_intel.app_state import security_report_path

    path = security_report_path(Settings.load())
    if not path.is_file():
        raise HTTPException(status_code=404, detail={"code": "report_missing"})
    return FileResponse(path, media_type="text/markdown", filename="security_release_report.md")


@app.get("/api/repository-health/tests")
def repository_health_tests() -> dict:
    from dep_intel.app_state import health_tests

    return health_tests(Settings.load())


class HealthRefreshBody(BaseModel):
    sections: list[str] = Field(default_factory=lambda: ["packages", "security", "tests"])


@app.post("/api/repository-health/refresh")
async def repository_health_refresh(body: HealthRefreshBody) -> dict:
    from dep_intel.app_state import run_repository_health

    settings = Settings.load()
    cluster = get_engine(settings)
    loop = asyncio.get_running_loop()

    def _job(active) -> dict:
        def on_progress(stage: str, percent: int, message: str) -> None:
            cluster.update_job_progress(active.id, percent)
            loop.call_soon_threadsafe(
                publish,
                "progress",
                {
                    "operation": "health_refresh",
                    "job_id": active.id,
                    "job_kind": "repository_health",
                    "stage": stage,
                    "percent": percent,
                    "message": message,
                },
            )

        return run_repository_health(settings, body.sections, on_progress)

    job = cluster.submit("repository_health", _job)
    return {"job_id": job.id, "status": job.status, "name": job.name}


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

    def publish_progress(phase: str, percent: int, message: str) -> None:
        loop.call_soon_threadsafe(
            publish,
            "progress",
            {"operation": "intelligence_refresh", "phase": phase, "stage": phase, "percent": percent, "message": message},
        )

    def on_nested(phase: str, percent: int, message: str) -> None:
        if phase == "complete":
            return
        mapped = 55 if phase == "analyze_commit_history" else 20
        plain = "Analyzing change history" if phase == "analyze_commit_history" else "Building repository structure"
        publish_progress(phase, min(mapped, 80), plain)

    async def job() -> None:
        global _running
        try:
            publish_progress("preparing", 8, "Preparing repositories")
            await asyncio.to_thread(run_graphs, "", "", 365, on_nested)
            publish_progress("contracts", 70, "Discovering contracts")
            await asyncio.to_thread(get_engine(Settings.load()).refresh)
            publish_progress("relationships", 82, "Resolving service relationships")
            publish_progress("topology", 90, "Building topology")
            try:
                from dep_intel.topology.discovery import discover

                await asyncio.to_thread(discover, Settings.load())
            except Exception:
                publish_progress("topology", 90, "Topology discovery could not finish. Workspace intelligence is partial.")
            publish("file_updated", {"filename": "snapshot"})
            publish_progress("publish", 96, "Publishing intelligence")
            publish_progress("complete", 100, "Intelligence refreshed")
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


@app.get("/api/cluster/catalog")
async def cluster_catalog(service: str = "") -> dict:
    payload = await asyncio.to_thread(get_engine(Settings.load()).catalog, service)
    if payload.get("error") == "unknown_service":
        raise HTTPException(status_code=404, detail=payload)
    return payload


@app.get("/api/cluster/relationships")
async def cluster_relationships(limit: int = 50, service: str = "", kind: str = "") -> dict:
    return await asyncio.to_thread(get_engine(Settings.load()).relationships, limit, service, kind)


@app.get("/api/search")
async def cluster_search(q: str = "", limit: int = 20) -> dict:
    return await asyncio.to_thread(get_engine(Settings.load()).search, q, limit)


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
        "path_scoring": ["coverage_adjusted", "raw"],
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
    return await asyncio.to_thread(engine.change_view, body.model_dump())


@app.get("/api/review/source-options")
def review_source_options(repository: str = "") -> dict:
    from dep_intel.review_source_options import source_options

    settings = Settings.load()
    repo = Path(repository)
    if not repo.is_absolute():
        repo = settings.root / repository
    return source_options(repo, settings)


@app.get("/api/reviews")
def reviews_list(limit: int = 20) -> dict:
    from dep_intel.review_store import list_reviews

    return {"reviews": list_reviews(Settings.load().output_dir, limit)}


@app.post("/api/review/run")
async def review_run(body: ReviewRunBody) -> dict:
    engine = _review_engine()
    payload = body.model_dump()
    if payload.get("mode") in {"exact", "auto"}:
        cluster = get_engine(Settings.load())
        review_id = uuid.uuid4().hex
        payload["review_id"] = review_id

        def _job(active) -> dict:
            payload["job_id"] = active.id
            publish("review_started", {"job_id": active.id, "review_id": review_id, "mode": payload.get("mode")})
            return engine.run(
                payload,
                cancel=active.cancel,
                on_progress=lambda percent: cluster.update_job_progress(active.id, percent),
            )

        job = cluster.submit("boundary_review", _job)
        return {"job_id": job.id, "review_id": review_id, "status": job.status, "name": job.name}
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
    return FileResponse(path, media_type="text/markdown", filename=f"nami-review-{review_id[:8]}.md")


def _review_export(review_id: str, kind: str, media_type: str, filename: str) -> FileResponse:
    from dep_intel.review_export import ensure_export

    try:
        path = ensure_export(Settings.load().output_dir, review_id, kind)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail={"code": "review_not_found"}) from None
    return FileResponse(path, media_type=media_type, filename=filename)


@app.get("/api/review/{review_id}/export/json")
async def review_export_json(review_id: str) -> FileResponse:
    return await asyncio.to_thread(_review_export, review_id, "json", "application/json", f"nami-review-{review_id[:8]}.json")


@app.get("/api/review/{review_id}/export/pdf")
async def review_export_pdf(review_id: str) -> FileResponse:
    return await asyncio.to_thread(_review_export, review_id, "pdf", "application/pdf", f"nami-review-{review_id[:8]}.pdf")


@app.get("/api/review/{review_id}/export/evidence")
async def review_export_evidence(review_id: str) -> FileResponse:
    return await asyncio.to_thread(_review_export, review_id, "zip", "application/zip", f"nami-review-{review_id[:8]}-evidence.zip")


class ImpactSummaryBody(BaseModel):
    root: dict[str, Any] = Field(default_factory=dict)
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    ranked_candidates: list[dict[str, Any]] = Field(default_factory=list)


@app.get("/api/topology")
def topology_status() -> dict:
    from dep_intel.topology.discovery import load_effective

    payload = load_effective(Settings.load())
    if payload is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "topology_not_discovered",
                "message": "Service topology has not been discovered for this workspace.",
                "recommended_action": "Refresh intelligence, or run scripts/nami-topology.py discover.",
            },
        )
    return payload


@app.post("/api/topology/discover")
def topology_discover() -> dict:
    from dep_intel.topology.discovery import discover

    return discover(Settings.load())


@app.get("/api/research/gate-validation")
def research_gate_validation() -> dict:
    """Latest offline Boundary Gate validation corpus. Absent until evaluate-gate.py has run."""
    path = Settings.load().output_dir / "gate-validation" / "latest.json"
    if not path.is_file():
        raise HTTPException(status_code=404, detail={"code": "not_found"})
    payload = read_json(path.parent, path.name)
    if not isinstance(payload, dict):
        raise HTTPException(status_code=404, detail={"code": "not_found"})
    return payload


@app.post("/api/cluster/impact-summary")
def impact_summary(body: ImpactSummaryBody) -> dict:
    from dep_intel.review_explain import summarize_impact

    return summarize_impact(body.model_dump())


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


class IntelligenceReportBody(BaseModel):
    scope: str
    service: str = ""
    graph_version: str = ""
    refresh: bool = False


@app.post("/api/reports")
async def create_intelligence_report(body: IntelligenceReportBody) -> dict:
    return await asyncio.to_thread(_create_intelligence_report, body.scope, body.service, body.graph_version, body.refresh)


def _create_intelligence_report(scope: str, service: str, graph_version: str, refresh: bool) -> dict:
    from dep_intel.reporting.builder import ReportError, generate_report

    settings = Settings.load()
    engine = get_engine(settings)
    if refresh:
        refreshed = engine.refresh()
        if refreshed.get("status") not in {"ok", None}:
            raise HTTPException(status_code=409, detail={"code": "refresh_failed", "status": refreshed.get("status"), "errors": refreshed.get("errors") or []})
        if graph_version and refreshed.get("version") not in {graph_version, None, ""}:
            raise HTTPException(status_code=409, detail={"code": "graph_version_mismatch", "graph_version": refreshed.get("version"), "requested": graph_version})
    snapshot = engine.current()
    try:
        document = generate_report(settings, snapshot, scope, service, graph_version)
    except ReportError as exc:
        raise HTTPException(status_code=409, detail={"code": "report_unavailable", "message": str(exc)}) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail={"code": "unknown_service", "service": service}) from exc
    return {
        "report_id": document["report_id"],
        "scope": document["scope"],
        "status": "complete",
        "generated_at": document["generated_at"],
        "graph_version": document["reproducibility"]["graph_version"],
        "readiness": (document.get("executive_summary") or {}).get("readiness"),
    }


@app.get("/api/reports")
def list_intelligence_reports(limit: int = 20) -> dict:
    from dep_intel.reporting.store import list_reports

    return {"reports": list_reports(Settings.load().output_dir, limit)}


@app.get("/api/reports/{report_id}")
def get_intelligence_report(report_id: str) -> dict:
    from dep_intel.reporting.store import read_report

    settings = Settings.load()
    document = read_report(settings.output_dir, report_id)
    if document is None:
        raise HTTPException(status_code=404, detail={"code": "report_not_found"})
    from dep_intel.reporting.collect import with_review_explanations

    return with_review_explanations(settings, document)


def _intelligence_export(report_id: str, kind: str, media_type: str) -> FileResponse:
    from dep_intel.reporting.package import ensure_export, public_filename

    settings = Settings.load()
    try:
        path = ensure_export(settings.output_dir, report_id, kind)
        filename = public_filename(settings.output_dir, report_id, kind)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail={"code": "report_not_found"}) from None
    return FileResponse(path, media_type=media_type, filename=filename)


@app.get("/api/reports/{report_id}/export/pdf")
async def intelligence_export_pdf(report_id: str) -> FileResponse:
    return await asyncio.to_thread(_intelligence_export, report_id, "pdf", "application/pdf")


@app.get("/api/reports/{report_id}/export/markdown")
async def intelligence_export_markdown(report_id: str) -> FileResponse:
    return await asyncio.to_thread(_intelligence_export, report_id, "markdown", "text/markdown")


@app.get("/api/reports/{report_id}/export/json")
async def intelligence_export_json(report_id: str) -> FileResponse:
    return await asyncio.to_thread(_intelligence_export, report_id, "json", "application/json")


@app.get("/api/reports/{report_id}/export/evidence")
async def intelligence_export_evidence(report_id: str) -> FileResponse:
    return await asyncio.to_thread(_intelligence_export, report_id, "zip", "application/zip")


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
