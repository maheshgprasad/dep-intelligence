"""Cluster change review orchestration.

Inspects every repository in a service cluster, records repositories with no
change, and runs the existing Boundary Gate pipeline for repositories that
changed. This module does not score impact or decide a Gate.
"""

from __future__ import annotations

import logging
import threading
import uuid
from pathlib import Path
from typing import Any, Callable

from dep_intel.cluster_interdependencies import copy_summary, fold_assessment, public_analysis
from dep_intel.config import Settings
from dep_intel.review_progress import CLUSTER_STAGE_LABELS, REVIEW_STAGES
from dep_intel.review_scope import ReviewScopeError, display_cluster_label, ensure_cluster_reference
from dep_intel.service_clusters import get_service_cluster
from dep_intel.store import now, read_json, write_json

log = logging.getLogger("nami.cluster.review")

ACTIVE_STATUSES = {"inspecting", "reviewing"}
INSPECTED_STATUSES = {"no_change", "completed", "failed"}
TERMINAL_REVIEW = {"completed", "failed", "no_change", "cancelled"}
ANALYSIS_STAGES = (
    ("collect_change_set", "Collecting cluster change set"),
    ("build_before_state", "Building dependency baseline"),
    ("build_after_state", "Building changed dependency state"),
    ("compare_interdependencies", "Comparing interdependencies"),
    ("correlate_changes", "Correlating repository changes"),
    ("synthesize_cluster", "Building cluster assessment"),
    ("write_cluster_report", "Generating cluster report"),
)

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def review_stage_catalog() -> list[dict[str, str]]:
    return [{"stage": stage, "label": CLUSTER_STAGE_LABELS.get(stage, label)} for stage, label in REVIEW_STAGES]


def cluster_review_dir(output_dir: Path, cluster_review_id: str) -> Path:
    return output_dir / "cluster-reviews" / cluster_review_id


def load_cluster_review(output_dir: Path, cluster_review_id: str) -> dict[str, Any] | None:
    payload = read_json(cluster_review_dir(output_dir, cluster_review_id), "state.json")
    if not isinstance(payload, dict):
        return None
    return payload


def save_cluster_review(output_dir: Path, record: dict[str, Any]) -> None:
    write_json(cluster_review_dir(output_dir, str(record["cluster_review_id"])), "state.json", record)


def _lock_for(cluster_review_id: str) -> threading.Lock:
    with _LOCKS_GUARD:
        lock = _LOCKS.get(cluster_review_id)
        if lock is None:
            lock = threading.Lock()
            _LOCKS[cluster_review_id] = lock
        return lock


def new_member(service_id: str, repository: str, repository_path: str, index: int) -> dict[str, Any]:
    return {
        "service_id": service_id,
        "repository": repository,
        "repository_path": repository_path,
        "repository_index": index,
        "status": "pending",
        "stage": "",
        "stage_label": "",
        "stage_index": 0,
        "stage_count": len(REVIEW_STAGES),
        "change_detected": False,
        "changed_file_count": 0,
        "review_id": "",
        "decision": "",
        "validation_status": "",
        "affected_services": [],
        "error_code": "",
        "error_summary": "",
    }


def new_cluster_review(
    *,
    cluster_id: str,
    cluster_label: str,
    source: dict[str, Any],
    members: list[dict[str, Any]],
    mode: str = "fast",
    verify: bool = True,
    cluster_review_id: str = "",
) -> dict[str, Any]:
    identity = cluster_review_id or uuid.uuid4().hex
    repositories = []
    for index, member in enumerate(members, start=1):
        repositories.append(
            new_member(
                str(member.get("service_id") or ""),
                str(member.get("repository") or member.get("service_id") or ""),
                str(member.get("repository_path") or ""),
                index,
            )
        )
    record = {
        "cluster_review_id": identity,
        "cluster_id": cluster_id,
        "cluster_label": cluster_label,
        "status": "running",
        "job_id": "",
        "mode": mode,
        "verify": verify,
        "source": {
            "kind": str(source.get("kind") or "working_tree"),
            "repository": str(source.get("repository") or ""),
            "base_ref": source.get("base_ref") or "",
            "head_ref": source.get("head_ref") or "",
            "commit": source.get("commit") or "",
            "pr_url": source.get("pr_url") or "",
            "since": source.get("since") or "",
            "until": source.get("until") or "",
        },
        "repository_total": len(repositories),
        "sequence": 1,
        "cancel_requested": False,
        "started_at": now(),
        "updated_at": now(),
        "repositories": repositories,
        "assessment": None,
        "counters": aggregate_counters(repositories),
    }
    return record


def aggregate_counters(repositories: list[dict[str, Any]]) -> dict[str, int]:
    total = len(repositories)
    inspected = sum(1 for item in repositories if item.get("status") in INSPECTED_STATUSES)
    pending = sum(1 for item in repositories if item.get("status") == "pending")
    running = sum(1 for item in repositories if item.get("status") in ACTIVE_STATUSES)
    with_changes = sum(1 for item in repositories if item.get("change_detected"))
    no_change = sum(1 for item in repositories if item.get("status") == "no_change")
    completed = sum(1 for item in repositories if item.get("status") == "completed")
    failed = sum(1 for item in repositories if item.get("status") == "failed")
    cancelled = sum(1 for item in repositories if item.get("status") == "cancelled")
    return {
        "repository_total": total,
        "repositories_inspected": inspected,
        "repositories_pending": pending,
        "repositories_running": running,
        "repositories_with_changes": with_changes,
        "repositories_no_change": no_change,
        "reviews_completed": completed,
        "reviews_failed": failed,
        "repositories_cancelled": cancelled,
    }


def public_member(member: dict[str, Any]) -> dict[str, Any]:
    return {
        "service_id": member.get("service_id") or "",
        "repository": member.get("repository") or "",
        "repository_index": member.get("repository_index") or 0,
        "status": member.get("status") or "pending",
        "stage": member.get("stage") or "",
        "stage_label": member.get("stage_label") or "",
        "stage_index": member.get("stage_index") or 0,
        "stage_count": member.get("stage_count") or len(REVIEW_STAGES),
        "change_detected": bool(member.get("change_detected")),
        "changed_file_count": int(member.get("changed_file_count") or 0),
        "review_id": member.get("review_id") or "",
        "decision": member.get("decision") or "",
        "validation_status": member.get("validation_status") or "",
        "error_code": member.get("error_code") or "",
        "error_summary": member.get("error_summary") or "",
        "commits": list(member.get("commits") or []),
        "commit_count": int(member.get("commit_count") or 0),
    }


def public_cluster_review(record: dict[str, Any]) -> dict[str, Any]:
    repositories = [public_member(item) for item in record.get("repositories") or []]
    counters = aggregate_counters(record.get("repositories") or [])
    running = [item for item in repositories if item["status"] in ACTIVE_STATUSES]
    total = counters["repository_total"] or 1
    fractional = counters["repositories_inspected"]
    for item in record.get("repositories") or []:
        if item.get("status") != "reviewing":
            continue
        count = int(item.get("stage_count") or 0) or len(REVIEW_STAGES)
        index = int(item.get("stage_index") or 0)
        if count:
            fractional += index / count
    percent = int(min(100, max(0, fractional / total * 100)))
    return {
        "cluster_review_id": record.get("cluster_review_id") or "",
        "cluster_id": record.get("cluster_id") or "",
        "cluster_label": record.get("cluster_label") or "",
        "status": record.get("status") or "running",
        "job_id": record.get("job_id") or "",
        "sequence": int(record.get("sequence") or 0),
        "cancel_requested": bool(record.get("cancel_requested")),
        "repository_total": counters["repository_total"],
        "percent": percent,
        "started_at": record.get("started_at") or "",
        "updated_at": record.get("updated_at") or "",
        "source_kind": (record.get("source") or {}).get("kind") or "",
        "counters": counters,
        "repositories": repositories,
        "running": running,
        "review_stages": review_stage_catalog(),
        "assessment": record.get("assessment"),
        "analysis_stage": record.get("analysis_stage") or "",
        "analysis_label": record.get("analysis_label") or "",
        "source": {
            "kind": (record.get("source") or {}).get("kind") or "",
            "since": (record.get("source") or {}).get("since") or "",
            "until": (record.get("source") or {}).get("until") or "",
        },
        "interdependencies": public_analysis(record.get("interdependencies")),
        "copy_summary": copy_summary(record),
    }


def cluster_progress_event(record: dict[str, Any], member: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = public_cluster_review(record)
    if member is None:
        return payload
    public = public_member(member)
    payload.update(
        {
            "repository": public["repository"],
            "service_id": public["service_id"],
            "repository_index": public["repository_index"],
            "repository_status": public["status"],
            "stage": public["stage"],
            "stage_label": public["stage_label"],
            "change_detected": public["change_detected"],
            "changed_file_count": public["changed_file_count"],
            "review_id": public["review_id"],
            "decision": public["decision"],
            "validation_status": public["validation_status"],
            "error_code": public["error_code"],
            "error_summary": public["error_summary"],
        }
    )
    return payload


def prepare_cluster_review(settings: Settings, body: dict[str, Any], snapshot: Any) -> dict[str, Any]:
    ensure_cluster_reference(snapshot, body.get("review_scope") or "service_cluster", body.get("cluster_id"))
    cluster_id = str(body.get("cluster_id") or "").strip()
    cluster = get_service_cluster(snapshot, cluster_id)
    if cluster is None:
        raise ReviewScopeError("unknown_cluster", "The selected service cluster is not in the published snapshot.")
    members = _members_for_cluster(settings, cluster)
    _validate_timeframe(body.get("source") or {})
    if not members:
        raise ReviewScopeError("cluster_empty", "The selected service cluster has no repositories to inspect.")
    return new_cluster_review(
        cluster_id=str(cluster.get("cluster_id") or cluster_id),
        cluster_label=display_cluster_label(cluster),
        source=dict(body.get("source") or {}),
        members=members,
        mode=str(body.get("mode") or "fast"),
        verify=bool(body.get("verify", True)),
    )


def preview_cluster_review(settings: Settings, body: dict[str, Any], snapshot: Any) -> dict[str, Any]:
    """Change presence only. Does not calculate impact or run Boundary Gate."""
    record = prepare_cluster_review(settings, body, snapshot)
    source = dict(body.get("source") or {})
    origin = str(source.get("repository") or "")
    repositories = []
    for member in record["repositories"]:
        detected = detect_repository_change(
            Path(str(member.get("repository_path") or "")),
            source,
            settings,
            identity={str(member.get("service_id") or ""), str(member.get("repository") or "")},
            required=_is_origin(member, origin),
        )
        repositories.append(
            {
                "service_id": member["service_id"],
                "repository": member["repository"],
                "repository_index": member["repository_index"],
                "change_detected": bool(detected.get("change_detected")),
                "changed_file_count": int(detected.get("changed_file_count") or 0),
                "error_code": detected.get("error_code") or "",
                "error_summary": detected.get("error_summary") or "",
                "commits": detected.get("commits") or [],
                "commit_count": int(detected.get("commit_count") or 0),
            }
        )
    changed = [item for item in repositories if item["change_detected"]]
    return {
        "cluster_id": record["cluster_id"],
        "cluster_label": record["cluster_label"],
        "repository_total": len(repositories),
        "changed_count": len(changed),
        "no_change_count": len(repositories) - len(changed),
        "repositories": repositories,
    }


def execute_cluster_review(
    record: dict[str, Any],
    *,
    settings: Settings,
    body: dict[str, Any],
    publish: Callable[[str, dict], None],
    cancel: threading.Event | None = None,
    detect: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]] | None = None,
    review: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]] | None = None,
    on_progress: Callable[[int], None] | None = None,
    cluster: Any | None = None,
) -> dict[str, Any]:
    runner = _ClusterRun(
        record,
        settings=settings,
        body=body,
        publish=publish,
        cancel=cancel,
        detect=detect,
        review=review,
        on_progress=on_progress,
        cluster=cluster,
    )
    return runner.run()


def request_cluster_cancel(output_dir: Path, cluster_review_id: str, cancel_job: Callable[[str], None] | None = None) -> dict[str, Any] | None:
    record = load_cluster_review(output_dir, cluster_review_id)
    if record is None:
        return None
    lock = _lock_for(cluster_review_id)
    with lock:
        current = load_cluster_review(output_dir, cluster_review_id) or record
        if current.get("status") in {"completed", "incomplete", "cancelled"}:
            return public_cluster_review(current)
        current["cancel_requested"] = True
        current["updated_at"] = now()
        save_cluster_review(output_dir, current)
        job_id = str(current.get("job_id") or "")
    if cancel_job is not None and job_id:
        cancel_job(job_id)
    return public_cluster_review(load_cluster_review(output_dir, cluster_review_id) or current)


class _ClusterRun:
    def __init__(
        self,
        record: dict[str, Any],
        *,
        settings: Settings,
        body: dict[str, Any],
        publish: Callable[[str, dict], None],
        cancel: threading.Event | None,
        detect: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]] | None,
        review: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]] | None,
        on_progress: Callable[[int], None] | None,
        cluster: Any | None,
    ) -> None:
        self.record = record
        self.settings = settings
        self.body = body
        self.publish = publish
        self.cancel = cancel or threading.Event()
        self.detect = detect
        self.review = review
        self.on_progress = on_progress
        self.cluster = cluster
        self.output_dir = settings.output_dir
        self.lock = _lock_for(str(record["cluster_review_id"]))

    def run(self) -> dict[str, Any]:
        self._emit(None)
        source = dict(self.body.get("source") or self.record.get("source") or {})
        origin = str(source.get("repository") or "")
        for member in self.record["repositories"]:
            if self._cancelled():
                self._cancel_from(member)
                break
            self._transition(
                member,
                status="inspecting",
                stage="detect_change",
                stage_label=CLUSTER_STAGE_LABELS["detect_change"],
                error_code="",
                error_summary="",
            )
            if self._cancelled():
                self._cancel_from(member)
                break
            detected = self._detect(member, source, origin)
            if not detected.get("ok", True) and detected.get("error_code"):
                self._transition(
                    member,
                    status="failed",
                    stage="",
                    stage_label="",
                    change_detected=False,
                    changed_file_count=int(detected.get("changed_file_count") or 0),
                    error_code=str(detected.get("error_code") or "inspection_failed"),
                    error_summary=str(detected.get("error_summary") or "This repository could not be inspected."),
                )
                continue
            changed = bool(detected.get("change_detected"))
            count = int(detected.get("changed_file_count") or 0)
            if not changed:
                self._transition(
                    member,
                    status="no_change",
                    stage="",
                    stage_label="No changes",
                    change_detected=False,
                    changed_file_count=0,
                    decision="",
                    review_id="",
                    validation_status="",
                    commits=[],
                    commit_count=0,
                    window_base="",
                    window_head="",
                )
                continue
            if self._cancelled():
                self._cancel_from(member, change_detected=True, changed_file_count=count)
                break
            self._transition(
                member,
                status="reviewing",
                stage="resolve_change",
                stage_label=CLUSTER_STAGE_LABELS["resolve_change"],
                change_detected=True,
                changed_file_count=count,
                commits=list(detected.get("commits") or []),
                commit_count=int(detected.get("commit_count") or 0),
                window_base=str(detected.get("base_ref") or ""),
                window_head=str(detected.get("head_ref") or ""),
            )
            try:
                report = self._review_member(member)
            except Exception as exc:
                self._transition(
                    member,
                    status="failed",
                    stage="",
                    stage_label="",
                    change_detected=True,
                    changed_file_count=count,
                    error_code="review_failed",
                    error_summary=_summary(exc),
                )
                continue
            self._apply_report(member, report, count)
            if self._cancelled() or _was_cancelled(report):
                self._cancel_after(member)
                break
        self._finish()
        return public_cluster_review(self.record)

    def _detect(self, member: dict[str, Any], source: dict[str, Any], origin: str) -> dict[str, Any]:
        if self.detect is not None:
            return self.detect(member, source)
        return detect_repository_change(
            Path(str(member.get("repository_path") or "")),
            source,
            self.settings,
            identity={str(member.get("service_id") or ""), str(member.get("repository") or "")},
            required=_is_origin(member, origin),
        )

    def _review_member(self, member: dict[str, Any]) -> dict[str, Any]:
        child = _child_body(self.record, self.body, member)
        head = str(member.get("window_head") or "")
        base = str(member.get("window_base") or "")
        if head and str((child.get("source") or {}).get("kind") or "") == "timeframe":
            source = dict(child["source"])
            if base:
                source.update(kind="range", base_ref=base, head_ref=head, commit=None)
            else:
                source.update(kind="commit", commit=head, base_ref=None, head_ref=None)
            child["source"] = source
        if self.review is not None:
            return self.review(member, child)
        from dep_intel.review_engine import ReviewEngine

        engine = ReviewEngine(self.settings, cluster=self.cluster, publish=self._on_engine_event)
        return engine.run(child, cancel=self.cancel, on_progress=self.on_progress)

    def _on_engine_event(self, event: str, data: dict) -> None:
        try:
            self.publish(event, data)
        except Exception:
            pass
        if event != "review_progress":
            return
        service_id = str(data.get("service_id") or "")
        member = next((item for item in self.record["repositories"] if item.get("service_id") == service_id), None)
        if member is None or member.get("status") in {"completed", "failed", "no_change", "cancelled"}:
            return
        self._transition(
            member,
            status="reviewing",
            stage=str(data.get("stage") or member.get("stage") or ""),
            stage_label=str(data.get("stage_label") or member.get("stage_label") or ""),
            stage_index=int(data.get("stage_index") or 0),
            stage_count=int(data.get("stage_count") or len(REVIEW_STAGES)),
            review_id=str(data.get("review_id") or member.get("review_id") or ""),
            change_detected=True,
        )

    def _apply_report(self, member: dict[str, Any], report: dict[str, Any], count: int) -> None:
        if not isinstance(report, dict) or not report.get("review_id"):
            self._transition(
                member,
                status="failed",
                change_detected=True,
                changed_file_count=count,
                error_code="review_failed",
                error_summary="The repository review did not return a stored result.",
            )
            return
        gate = report.get("gate") if isinstance(report.get("gate"), dict) else {}
        validation = report.get("gate_validation") if isinstance(report.get("gate_validation"), dict) else {}
        scope = report.get("review_scope") if isinstance(report.get("review_scope"), dict) else {}
        affected = [str(item) for item in scope.get("affected_member_services") or [] if item]
        if not affected:
            members = {str(item.get("service_id") or "") for item in self.record["repositories"]}
            impact = report.get("impact") if isinstance(report.get("impact"), dict) else {}
            affected = [str(item) for item in impact.get("affected_services") or [] if str(item) in members]
        decision = str(gate.get("decision") or "")
        cancelled = _was_cancelled(report)
        self._transition(
            member,
            status="cancelled" if cancelled else "completed",
            stage="" if cancelled else "write_report",
            stage_label="Cancelled" if cancelled else "Complete",
            change_detected=True,
            changed_file_count=count,
            review_id=str(report.get("review_id") or ""),
            decision="" if cancelled else decision,
            validation_status="" if cancelled else str(validation.get("status") or ""),
            affected_services=[] if cancelled else affected,
            reason_codes=[] if cancelled else [str(item) for item in gate.get("reason_codes") or [] if item],
            contract_changes=[] if cancelled else [item for item in report.get("contract_changes") or [] if isinstance(item, dict)],
            error_code="cancelled" if cancelled else "",
            error_summary="The repository review was cancelled." if cancelled else "",
        )

    def _cancelled(self) -> bool:
        with self.lock:
            requested = bool(self.record.get("cancel_requested"))
        return requested or self.cancel.is_set()

    def _cancel_from(self, member: dict[str, Any], **extra: Any) -> None:
        started = False
        for item in self.record["repositories"]:
            if item is member:
                started = True
            if not started:
                continue
            if item.get("status") in {"completed", "no_change", "failed"}:
                continue
            fields: dict[str, Any] = {"status": "cancelled", "stage": "", "stage_label": "Cancelled"}
            if item is member:
                fields.update(extra)
            self._transition(item, **fields)

    def _cancel_after(self, member: dict[str, Any]) -> None:
        seen = False
        for item in self.record["repositories"]:
            if item is member:
                seen = True
                continue
            if not seen:
                continue
            if item.get("status") in {"completed", "no_change", "failed"}:
                continue
            self._transition(item, status="cancelled", stage="", stage_label="Cancelled")

    def _finish(self) -> None:
        statuses = [str(item.get("status") or "") for item in self.record["repositories"]]
        if self._cancelled() or any(item == "cancelled" for item in statuses):
            status = "cancelled"
        elif any(item == "failed" for item in statuses) or any(item.get("decision") == "incomplete" for item in self.record["repositories"]):
            status = "incomplete"
        else:
            status = "completed"
        analysis = None
        if status != "cancelled":
            try:
                analysis = self._analyze()
            except Exception:
                log.exception("cluster interdependency analysis failed")
                analysis = {
                    "comparison_mode": "partial",
                    "comparison_coverage": {"services_total": len(self.record.get("repositories") or []), "services_partial": []},
                    "summary": {},
                    "items": [],
                    "changed_services": [],
                    "affected_services": [],
                    "unchanged_but_affected_services": [],
                    "temporal_context": [],
                    "change_set": {},
                    "limitations": ["Cluster interdependency analysis incomplete."],
                    "diagnostic": {
                        "code": "analysis_incomplete",
                        "stage": self.record.get("analysis_stage") or "compare_interdependencies",
                        "message": "Cluster interdependency analysis incomplete",
                    },
                }
                self.record["interdependencies"] = analysis
        if self._cancelled() or (isinstance(analysis, dict) and analysis.get("cancelled")):
            status = "cancelled"
        elif isinstance(analysis, dict) and (analysis.get("diagnostic") or {}).get("code") == "analysis_incomplete" and status == "completed":
            status = "incomplete"
        assessment = synthesize_assessment(self.record["repositories"], status)
        if analysis is not None:
            assessment = fold_assessment(assessment, analysis, status)
        with self.lock:
            self.record["status"] = status
            self.record["assessment"] = assessment
            self.record["counters"] = aggregate_counters(self.record["repositories"])
            self.record["sequence"] = int(self.record.get("sequence") or 0) + 1
            self.record["updated_at"] = now()
            save_cluster_review(self.output_dir, self.record)
            payload = cluster_progress_event(self.record)
        self._publish("cluster_review_completed" if status != "cancelled" else "cluster_review_cancelled", payload)
        self._publish("cluster_review_progress", payload)
        if self.on_progress is not None:
            self.on_progress(int(payload.get("percent") or 0))

    def _analyze(self) -> dict[str, Any]:
        from dep_intel.cluster_dependency_state import build_review_boundaries
        from dep_intel.cluster_interdependencies import analyze_interdependencies
        from dep_intel.service_clusters import confirmed_relationships

        source = dict(self.record.get("source") or {})
        self._analysis_stage("collect_change_set", "Collecting cluster change set")
        if self._cancelled():
            return {"cancelled": True, "comparison_mode": "partial", "summary": {}, "items": []}
        prepared = []
        for member in self.record["repositories"]:
            prepared.append({
                "service_id": member.get("service_id") or "",
                "changed": bool(member.get("change_detected")) and member.get("status") == "completed",
                "review_id": member.get("review_id") or "",
                "decision": member.get("decision") or "",
                "reason_codes": list(member.get("reason_codes") or []),
                "contract_changes": list(member.get("contract_changes") or []),
                "affected_services": list(member.get("affected_services") or []),
                "changed_file_count": int(member.get("changed_file_count") or 0),
                "commit_count": int(member.get("commit_count") or 0),
                "repository_path": member.get("repository_path") or "",
                "repository": member.get("repository") or "",
                "before_ref": "",
                "after_ref": "",
            })
        relationships = self._relationships(confirmed_relationships)
        reconstruct = any(str(item.get("repository_path") or "") and Path(str(item.get("repository_path"))).is_dir() for item in prepared)
        before_state = None
        after_state = None
        mode = "evidence_derived"
        coverage = None
        limitations = None
        if reconstruct:
            directory = cluster_review_dir(self.output_dir, str(self.record.get("cluster_review_id") or ""))

            def progress(kind: str, done: int, total: int) -> None:
                if kind == "before":
                    self._analysis_stage("build_before_state", f"Building before dependency state · {done} / {total} services")
                else:
                    self._analysis_stage("build_after_state", f"Building after dependency state · {done} / {total} services")

            self._analysis_stage("build_before_state", "Building before dependency state")
            built = build_review_boundaries(
                prepared,
                source,
                settings=self.settings,
                work_dir=directory / "worktrees",
                cancel=self._cancelled,
                on_progress=progress,
            )
            if built.get("cancelled") or self._cancelled():
                return {"cancelled": True, "comparison_mode": "partial", "summary": {}, "items": [], "before_state": built.get("before_state"), "after_state": built.get("after_state")}
            before_state = built.get("before_state")
            after_state = built.get("after_state")
            mode = str(built.get("comparison_mode") or "partial")
            coverage = built.get("comparison_coverage") or {}
            limitations = list(built.get("limitations") or [])
            examined = len((after_state or {}).get("relationships") or []) + len((before_state or {}).get("relationships") or [])
            self._analysis_stage("compare_interdependencies", f"Comparing {examined} relationship states")
        else:
            self._analysis_stage("compare_interdependencies", "Comparing interdependencies")
        if self._cancelled():
            return {"cancelled": True, "comparison_mode": "partial", "summary": {}, "items": []}
        self._analysis_stage("correlate_changes", "Correlating provider and consumer changes")
        analysis = analyze_interdependencies(
            prepared,
            None if reconstruct else relationships,
            source=source,
            cluster_id=str(self.record.get("cluster_id") or ""),
            comparison_mode=mode,
            before_state=before_state,
            after_state=after_state,
            comparison_coverage=coverage,
            limitations=limitations,
        )
        if reconstruct:
            # Published relationships are not the boundary. Temporal notes may still be attached.
            temporal = [item for item in relationships if str(item.get("provenance") or "").lower() in {"temporal", "temporal_only", "history", "cochange"} or item.get("temporal_only")]
            if temporal:
                from dep_intel.cluster_interdependencies import _temporal_note

                analysis["temporal_context"] = list(analysis.get("temporal_context") or []) + [_temporal_note(item) for item in temporal]
        self._analysis_stage("synthesize_cluster", "Building dependency paths")
        self._analysis_stage("synthesize_cluster", "Building cluster assessment")
        self._write_analysis(analysis)
        with self.lock:
            self.record["interdependencies"] = analysis
        self._analysis_stage("write_cluster_report", "Writing cluster report")
        return analysis

    def _write_analysis(self, analysis: dict[str, Any]) -> None:
        import json

        from dep_intel.path_hygiene import scrub_value

        directory = cluster_review_dir(self.output_dir, str(self.record.get("cluster_review_id") or "")) / "analysis"
        directory.mkdir(parents=True, exist_ok=True)
        files = {
            "before-state.json": analysis.get("before_state") or {},
            "after-state.json": analysis.get("after_state") or {},
            "comparison.json": {
                "comparison_mode": analysis.get("comparison_mode"),
                "comparison_coverage": analysis.get("comparison_coverage"),
                "summary": analysis.get("summary"),
                "items": analysis.get("items"),
                "dependency_paths": analysis.get("dependency_paths"),
                "limitations": analysis.get("limitations"),
            },
            "cluster-evidence.json": analysis.get("cluster_evidence") or [],
        }
        for name, body in files.items():
            (directory / name).write_text(json.dumps(scrub_value(body), indent=2, sort_keys=True), encoding="utf-8")

    def _relationships(self, confirmed_relationships) -> list[dict[str, Any]]:
        snapshot = None
        if self.cluster is not None and hasattr(self.cluster, "current"):
            try:
                snapshot = self.cluster.current()
            except Exception:
                snapshot = None
        if snapshot is None:
            return []
        members = {str(item.get("service_id") or "") for item in self.record["repositories"]}
        return [
            row for row in confirmed_relationships(snapshot)
            if str(row.get("from_service") or "") in members and str(row.get("to_service") or "") in members
        ]

    def _analysis_stage(self, stage: str, label: str) -> None:
        with self.lock:
            self.record["analysis_stage"] = stage
            self.record["analysis_label"] = label
            self.record["sequence"] = int(self.record.get("sequence") or 0) + 1
            self.record["updated_at"] = now()
            save_cluster_review(self.output_dir, self.record)
            payload = cluster_progress_event(self.record)
        self._publish("cluster_review_progress", payload)

    def _transition(self, member: dict[str, Any], **fields: Any) -> None:
        with self.lock:
            member.update(fields)
            self.record["counters"] = aggregate_counters(self.record["repositories"])
            self.record["sequence"] = int(self.record.get("sequence") or 0) + 1
            self.record["updated_at"] = now()
            self.record["repository_total"] = len(self.record["repositories"])
            save_cluster_review(self.output_dir, self.record)
            payload = cluster_progress_event(self.record, member)
        self._publish("cluster_review_progress", payload)
        if self.on_progress is not None:
            self.on_progress(int(payload.get("percent") or 0))

    def _emit(self, member: dict[str, Any] | None) -> None:
        with self.lock:
            self.record["counters"] = aggregate_counters(self.record["repositories"])
            self.record["updated_at"] = now()
            save_cluster_review(self.output_dir, self.record)
            payload = cluster_progress_event(self.record, member)
        self._publish("cluster_review_progress", payload)

    def _publish(self, event: str, payload: dict[str, Any]) -> None:
        try:
            self.publish(event, payload)
        except Exception:
            pass


def synthesize_assessment(repositories: list[dict[str, Any]], status: str) -> dict[str, Any]:
    changed = [item for item in repositories if item.get("change_detected") and item.get("status") != "no_change"]
    completed = [item for item in repositories if item.get("status") == "completed"]
    decisions = [str(item.get("decision") or "") for item in completed]
    blocks = sum(1 for item in decisions if item == "block")
    warns = sum(1 for item in decisions if item == "warn")
    passes = sum(1 for item in decisions if item == "pass")
    incomplete = sum(1 for item in decisions if item == "incomplete")
    failed = sum(1 for item in repositories if item.get("status") == "failed")
    evidenced: list[str] = []
    seen: set[str] = set()
    for item in completed:
        for service in item.get("affected_services") or []:
            if service and service not in seen:
                seen.add(service)
                evidenced.append(service)
    if status == "cancelled":
        code = "cancelled"
        label = "CANCELLED"
    elif blocks or warns:
        code = "attention_required"
        label = "ATTENTION REQUIRED"
    elif failed or incomplete or status == "incomplete":
        code = "incomplete"
        label = "INCOMPLETE"
    elif not changed and not failed:
        code = "no_changes"
        label = "NO CHANGES"
    else:
        code = "clear"
        label = "CLEAR"
    parts = []
    if blocks:
        parts.append(f"{blocks} verified blocker" if blocks == 1 else f"{blocks} verified blockers")
    if warns:
        parts.append(f"{warns} warning" if warns == 1 else f"{warns} warnings")
    if passes:
        parts.append(f"{passes} pass" if passes == 1 else f"{passes} passes")
    if incomplete:
        parts.append(f"{incomplete} incomplete")
    if failed:
        parts.append(f"{failed} failed")
    return {
        "code": code,
        "label": label,
        "summary": ", ".join(parts),
        "verified_blockers": blocks,
        "warnings": warns,
        "passes": passes,
        "incomplete": incomplete,
        "failed": failed,
        "repositories_with_changes": sum(1 for item in repositories if item.get("change_detected")),
        "repositories_no_change": sum(1 for item in repositories if item.get("status") == "no_change"),
        "evidenced_service_count": len(evidenced),
        "evidenced_services": evidenced,
        "complete": status == "completed",
    }


def detect_repository_change(
    repo: Path,
    source: dict[str, Any],
    settings: Settings,
    *,
    identity: set[str] | None = None,
    required: bool = False,
) -> dict[str, Any]:
    kind = str(source.get("kind") or "working_tree")
    names = {item for item in (identity or set()) if item}
    names.add(repo.name)
    if kind == "github_pr" and not _matches_source_repository(source, names):
        return _detection(False, 0)
    if not repo.is_dir():
        return _detection(False, 0, ok=False, error_code="repository_unavailable", error_summary="Repository checkout is not available.")
    if kind == "github_pr":
        return _detect_pull_request(repo, source, settings)
    if kind == "working_tree":
        return _name_only(repo, ["diff", "--name-only", "HEAD"])
    if kind == "staged":
        return _name_only(repo, ["diff", "--cached", "--name-only"])
    if kind == "commit":
        ref = str(source.get("commit") or "")
        return _ref_diff(repo, ref, ["diff-tree", "--no-commit-id", "--name-only", "-r", ref], required=required)
    if kind == "timeframe":
        return _timeframe(repo, source)
    if kind == "range":
        base = str(source.get("base_ref") or "")
        head = str(source.get("head_ref") or "")
        if not base or not head:
            return _detection(False, 0, ok=False, error_code="git_ref_not_found", error_summary="Base and head refs are required.")
        return _ref_diff(repo, base, ["diff", "--name-only", base, head], required=required, also=head)
    return _detection(False, 0, ok=False, error_code="invalid_source", error_summary="This change source is not supported for cluster inspection.")


def _members_for_cluster(settings: Settings, cluster: dict[str, Any]) -> list[dict[str, str]]:
    from dep_intel.repository_identity import load_index, resolve_repository_path

    index = load_index(settings)
    members = []
    for service_id in cluster.get("services") or []:
        service = str(service_id)
        record = index.resolve(service_id=service)
        repository = service
        path = ""
        if record is not None:
            repository = record.slug or record.service_id or service
            if record.checkout and Path(record.checkout).is_dir():
                path = record.checkout
        if not path:
            resolved = resolve_repository_path(settings, service)
            if resolved is not None:
                path = str(resolved)
                repository = resolved.name
        members.append({"service_id": service, "repository": repository, "repository_path": path})
    return members


def _child_body(record: dict[str, Any], body: dict[str, Any], member: dict[str, Any]) -> dict[str, Any]:
    source = dict(body.get("source") or {})
    source["repository"] = member.get("repository") or member.get("service_id")
    source["service_id"] = member.get("service_id")
    child: dict[str, Any] = {
        "source": source,
        "mode": body.get("mode") or record.get("mode") or "fast",
        "verify": bool(body.get("verify", record.get("verify", True))),
        "model": body.get("model") or {},
        "service": member.get("service_id") or "",
        "review_scope": "service_cluster",
        "cluster_id": record.get("cluster_id") or "",
        "job_id": record.get("job_id") or "",
        "cluster_context": {
            "cluster_review_id": record.get("cluster_review_id") or "",
            "cluster_id": record.get("cluster_id") or "",
            "cluster_label": record.get("cluster_label") or "",
            "repository": member.get("repository") or "",
            "service_id": member.get("service_id") or "",
            "repository_index": member.get("repository_index") or 0,
            "repository_total": record.get("repository_total") or 0,
        },
    }
    if body.get("config") is not None:
        child["config"] = body.get("config")
    return child


def _validate_timeframe(source: dict[str, Any]) -> None:
    if str(source.get("kind") or "") != "timeframe":
        return
    from dep_intel.commit_window import window_bounds
    from dep_intel.review_scope import ReviewScopeError

    try:
        window_bounds(str(source.get("since") or ""), str(source.get("until") or ""))
    except ValueError as exc:
        raise ReviewScopeError("invalid_timeframe", str(exc)) from exc


def _timeframe(repo: Path, source: dict[str, Any]) -> dict[str, Any]:
    from dep_intel.commit_window import commits_in_window

    window = commits_in_window(repo, str(source.get("since") or ""), str(source.get("until") or ""))
    if not window["ok"]:
        return _detection(False, 0, ok=False, error_code="inspection_failed", error_summary=str(window["error"] or "Commit history could not be read."))
    if not window["head_ref"]:
        return _detection(False, 0)
    if window["base_ref"]:
        detected = _ref_diff(repo, window["base_ref"], ["diff", "--name-only", window["base_ref"], window["head_ref"]], required=True, also=window["head_ref"])
    else:
        detected = _ref_diff(repo, window["head_ref"], ["diff-tree", "--no-commit-id", "--name-only", "-r", window["head_ref"]], required=True)
    if not detected.get("ok", True):
        return detected
    detected["change_detected"] = True
    detected["commits"] = window["commits"]
    detected["commit_count"] = window["commit_count"]
    detected["base_ref"] = window["base_ref"]
    detected["head_ref"] = window["head_ref"]
    return detected


def _is_origin(member: dict[str, Any], origin: str) -> bool:
    if not origin:
        return False
    names = {
        str(member.get("service_id") or ""),
        str(member.get("repository") or ""),
        str(member.get("repository_path") or ""),
    }
    names.discard("")
    if origin in names:
        return True
    checkout = str(member.get("repository_path") or "")
    if not checkout or not Path(origin).is_absolute():
        return False
    try:
        return Path(origin).resolve() == Path(checkout).resolve()
    except OSError:
        return False


def _matches_source_repository(source: dict[str, Any], identity: set[str]) -> bool:
    selected = str(source.get("repository") or "")
    if not selected:
        return True
    return selected in identity


def _detect_pull_request(repo: Path, source: dict[str, Any], settings: Settings) -> dict[str, Any]:
    from dep_intel.review_sources import resolve_change

    try:
        resolved = resolve_change(repo, source, settings)
    except Exception as exc:
        return _detection(False, 0, ok=False, error_code="inspection_failed", error_summary=_summary(exc))
    diagnostics = [item for item in resolved.get("diagnostics") or [] if item.get("severity") == "error"]
    count = _count_name_status(str(resolved.get("name_status") or ""))
    if diagnostics and count == 0:
        message = str(diagnostics[0].get("message") or "The pull request could not be inspected.")
        return _detection(False, 0, ok=False, error_code=str(diagnostics[0].get("code") or "inspection_failed"), error_summary=message[:240])
    return _detection(count > 0, count)


def _ref_diff(repo: Path, ref: str, args: list[str], *, required: bool, also: str = "") -> dict[str, Any]:
    if not _ref_exists(repo, ref) or (also and not _ref_exists(repo, also)):
        if required:
            return _detection(False, 0, ok=False, error_code="git_ref_not_found", error_summary="The selected revision is not in this repository.")
        return _detection(False, 0)
    return _name_only(repo, args)


def _ref_exists(repo: Path, ref: str) -> bool:
    from dep_intel.review_sources import run_git, safe_ref

    if not ref or not safe_ref(ref):
        return False
    # A full SHA passes `rev-parse --verify` even when that object is not in this
    # repository. Peeling to a commit requires the object to exist here.
    code, stdout, _stderr = run_git(repo, ["rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}"], timeout=8)
    return code == 0 and bool(stdout.strip())


def _name_only(repo: Path, args: list[str]) -> dict[str, Any]:
    from dep_intel.review_sources import run_git

    code, stdout, stderr = run_git(repo, args, timeout=8, limit=200_000)
    if code != 0:
        summary = (stderr or "Change detection failed.").strip().splitlines()
        text = summary[0] if summary else "Change detection failed."
        return _detection(False, 0, ok=False, error_code="inspection_failed", error_summary=text[:240])
    files = [line.strip() for line in stdout.splitlines() if line.strip()]
    return _detection(bool(files), len(files))


def _count_name_status(name_status: str) -> int:
    if not name_status:
        return 0
    if "\x00" in name_status:
        parts = [item for item in name_status.split("\x00") if item]
        return len(parts) // 2
    return len([line for line in name_status.splitlines() if line.strip()])


def _detection(change_detected: bool, count: int, *, ok: bool = True, error_code: str = "", error_summary: str = "") -> dict[str, Any]:
    return {
        "ok": ok,
        "change_detected": change_detected,
        "changed_file_count": count,
        "error_code": error_code,
        "error_summary": error_summary,
    }


def _was_cancelled(report: dict[str, Any]) -> bool:
    gate = report.get("gate") if isinstance(report.get("gate"), dict) else {}
    reasons = gate.get("reason_codes") or []
    control = report.get("review_control") if isinstance(report.get("review_control"), dict) else {}
    return bool(control.get("cancelled")) or "cancelled" in reasons


def _summary(exc: Exception) -> str:
    text = str(exc).strip() or exc.__class__.__name__
    return text.splitlines()[0][:240]
