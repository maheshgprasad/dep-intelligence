"""Nami Boundary Gate orchestration.

REST, MCP, and the CLI call this engine. It does not reimplement NRM scoring.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from dep_intel.cluster_manifest import ImpactSettings
from dep_intel.config import Settings
from dep_intel.graph_models import Snapshot
from dep_intel.hybrid_impact import HybridSettings, analyze_hybrid
from dep_intel.impact import analyze
from dep_intel.review_context import collect_context
from dep_intel.review_evidence import apply_evidence_gate
from dep_intel.review_feedback import record_feedback
from dep_intel.review_findings import build_findings
from dep_intel.review_gate import decide_gate
from dep_intel.review_model_provider import NoneProvider, model_configured, provider_from_env
from dep_intel.review_models import MODULE_ABBREV, MODULE_NAME, NBG_VERSION, ORIGIN_STATEMENT, SCORE_NOTE, TEMPORAL_LIMIT, diagnostic
from dep_intel.review_progress import progress_event, resolve_review_mode
from dep_intel.review_policy import evaluate_policies, load_review_config
from dep_intel.review_sources import materialize_worktree, remove_worktree, repository_allowed, resolve_change, retain_worktrees
from dep_intel.review_store import read_review, render_markdown, write_review
from dep_intel.review_verify import apply_verification_findings, run_verifications
from dep_intel.store import now, read_json, write_json
from dep_intel.change_view import build_change_view, graph_alignment

log = logging.getLogger("nami.boundary")

LIMITATIONS = [
    SCORE_NOTE,
    "NBG does not change structural_score, temporal_score, hybrid_score, or NRM weights.",
    "Automatic protobuf/gRPC discovery is not claimed. Manifest-declared contracts keep provenance manifest.",
    "Automatic event-broker discovery is not claimed.",
    "A model suggestion is advisory until the Evidence Gate accepts it.",
    "priority_score orders findings. It is not a probability and it does not make the gate decision.",
    TEMPORAL_LIMIT,
    "Exact review worktrees are private to the review run and are not published as the cluster snapshot.",
]


class ReviewEngine:
    def __init__(self, settings: Settings, cluster: Any | None = None, publish: Callable[[str, dict], None] | None = None, provider: Any | None = None) -> None:
        self.settings = settings
        self.cluster = cluster
        self.publish = publish or (lambda _event, _data: None)
        self.provider = provider

    def run(self, body: dict[str, Any], cancel: Any | None = None, on_progress: Callable[[int], None] | None = None) -> dict[str, Any]:
        started = time.perf_counter()
        review_id = str(body.get("review_id") or uuid.uuid4().hex)
        job_id = str(body.get("job_id") or "")
        self._on_progress = on_progress
        timings: dict[str, float] = {}
        self._event("review_started", {"review_id": review_id, "job_id": job_id})
        self._progress(review_id, "resolve_change", job_id)
        source = dict(body.get("source") or {})
        repo = _repository_path(source, self.settings)
        requested_mode = str(body.get("mode") or "fast")
        verify = bool(body.get("verify", True))
        model_enabled = bool((body.get("model") or {}).get("enabled", False))
        config, config_errors = load_review_config(repo if repo.exists() else None, self.settings.root, body.get("config"))
        if config_errors:
            report = self._finish_incomplete(review_id, repo, source, config_errors, started, timings, requested_mode=requested_mode)
            return report
        stage = time.perf_counter()
        if not repo.exists() or not repository_allowed(repo, self.settings):
            resolved = resolve_change(repo, source, self.settings, config.get("budgets"))
        else:
            resolved = resolve_change(repo, source, self.settings, config.get("budgets"))
        timings["change_resolution_ms"] = _ms(stage)
        fatal = [item for item in resolved.get("diagnostics") or [] if item.get("severity") == "error"]
        snapshot = self.cluster.current() if self.cluster is not None and hasattr(self.cluster, "current") else None
        previous = self.cluster.previous() if self.cluster is not None and hasattr(self.cluster, "previous") else None
        service_id = _service_id(snapshot, body.get("service") or source.get("service_id") or "", repo)
        if fatal:
            return self._finish_incomplete(review_id, repo, source, fatal, started, timings, resolved=resolved, service_id=service_id, requested_mode=requested_mode)
        stage = time.perf_counter()
        worktrees: list[Path] = []
        alignment = graph_alignment(snapshot, service_id, resolved.get("base_sha"), resolved.get("head_sha"), dirty=resolved.get("kind") in {"working_tree", "staged"})
        mode = resolve_review_mode(requested_mode, alignment)
        exact_snapshot = None
        if mode == "exact":
            exact_snapshot, alignment, worktrees, exact_diagnostics = self._exact(review_id, repo, resolved, service_id, snapshot, alignment)
            resolved["diagnostics"] = list(resolved.get("diagnostics") or []) + exact_diagnostics
        timings["graph_alignment_ms"] = _ms(stage)
        self._progress(review_id, "build_change_view", job_id)
        self._progress(review_id, "align_graph", job_id)
        active_snapshot = exact_snapshot or snapshot
        change = build_change_view(
            resolved,
            service_id=service_id,
            repository=str(repo),
            snapshot=active_snapshot,
            base_snapshot=previous or snapshot,
            repo_root=repo,
            budgets=config.get("budgets"),
            graph_alignment=alignment,
        )
        _mark_tested(change.get("changed_symbols") or [], active_snapshot)
        log.info("change_view.completed")
        cache_key = _cache_key(resolved, config, active_snapshot.version if active_snapshot is not None else "", mode, verify, model_enabled)
        cached = _read_cache(self.settings.output_dir, cache_key)
        if cached is not None and resolved.get("kind") not in {"working_tree", "staged"}:
            cached = {**cached, "cache": {"hit": True, "key": cache_key}}
            self._event("review_completed", {"review_id": cached.get("review_id"), "decision": (cached.get("gate") or {}).get("decision"), "cache": True})
            return cached
        as_of = resolved.get("committed_at") if resolved.get("kind") in {"commit", "range", "github_pr"} else None
        self._progress(review_id, "calculate_impact", job_id)
        stage = time.perf_counter()
        impact = self._impact(active_snapshot, change, as_of, service_id, exact=exact_snapshot is not None)
        timings["impact_ms"] = _ms(stage)
        log.info("impact.completed")
        self._progress(review_id, "apply_policies", job_id)
        stage = time.perf_counter()
        policies = evaluate_policies(config, change, repo)
        timings["policy_ms"] = _ms(stage)
        log.info("policy.completed")
        context = collect_context(repo, change, impact, config)
        model_summary = ""
        model_findings: list[dict] = []
        model_diagnostic = ""
        stage = time.perf_counter()
        if model_enabled:
            model_findings, model_summary, model_diagnostic = self._model(change, context, policies)
        timings["model_ms"] = _ms(stage) if model_enabled else 0.0
        self._progress(review_id, "collect_findings", job_id)
        findings = build_findings(policies, impact, change, model_findings=model_findings)
        self._progress(review_id, "verify_findings", job_id)
        stage = time.perf_counter()
        self._event("verification_started", {"review_id": review_id})
        verification = run_verifications(config, change, policies, repo, enabled=verify, cancel=cancel, secrets=_secrets(self.settings))
        self._event("verification_completed", {"review_id": review_id, "results": [item.get("status") for item in verification]})
        timings["verification_ms"] = _ms(stage)
        findings = apply_verification_findings(findings, verification)
        accepted, rejected = apply_evidence_gate(
            findings,
            change,
            repo,
            minimum=(config.get("gate") or {}).get("minimum_evidence") or "weak",
            alignment=alignment,
        )
        diagnostics = list(change.get("diagnostics") or []) + rejected
        if model_diagnostic:
            diagnostics.append(diagnostic(model_diagnostic, "The optional review model did not return usable findings.", severity="warning"))
        cancelled = cancel is not None and getattr(cancel, "is_set", lambda: False)()
        if cancelled:
            diagnostics.append(diagnostic("cancelled", "The review was cancelled.", severity="warning"))
        gate = decide_gate(accepted, verification, config, alignment=alignment, diagnostics=diagnostics)
        if cancelled:
            gate["decision"] = "incomplete"
            gate["blocking_findings"] = []
            gate["reason_codes"] = ["cancelled"]
            gate["evidence_complete"] = False
        self._progress(review_id, "gate_decision", job_id)
        self._progress(review_id, "write_report", job_id)
        timings["total_ms"] = _ms(started)
        report = _report(
            review_id,
            change,
            impact,
            policies,
            gate,
            verification,
            context,
            diagnostics,
            timings,
            config,
            model_summary,
            mode,
            as_of,
            requested_mode=requested_mode,
            resolved_mode=mode,
        )
        public_change = {key: value for key, value in change.items() if key != "_files"}
        write_review(self.settings.output_dir, review_id, {
            "manifest.json": _manifest(review_id, report, config, mode),
            "change_view.json": public_change,
            "context.json": context,
            "impact.json": impact,
            "policy_results.json": {"results": policies},
            "candidate_findings.json": {"findings": findings},
            "evidence.json": {"accepted": accepted, "rejected": rejected},
            "verification.json": {"commands": verification},
            "report.json": report,
            "report.md": render_markdown(report),
        })
        _write_cache(self.settings.output_dir, cache_key, review_id)
        self._cleanup(repo, worktrees)
        log.info("gate.completed")
        log.info("review.completed")
        self._event("review_completed", {"review_id": review_id, "decision": gate.get("decision")})
        return report

    def change_view(self, body: dict[str, Any]) -> dict[str, Any]:
        """Resolve and classify a change. Stops before impact, policy, model, verification, and the gate."""
        source = dict(body.get("source") or {})
        repo = _repository_path(source, self.settings)
        config, config_errors = load_review_config(repo if repo.exists() else None, self.settings.root, body.get("config"))
        resolved = resolve_change(repo, source, self.settings, config.get("budgets"))
        snapshot = self.cluster.current() if self.cluster is not None and hasattr(self.cluster, "current") else None
        service_id = _service_id(snapshot, body.get("service") or source.get("service_id") or "", repo)
        alignment = graph_alignment(snapshot, service_id, resolved.get("base_sha"), resolved.get("head_sha"), dirty=resolved.get("kind") in {"working_tree", "staged"})
        change = build_change_view(
            resolved,
            service_id=service_id,
            repository=str(repo),
            snapshot=snapshot,
            base_snapshot=snapshot,
            repo_root=repo,
            budgets=config.get("budgets"),
            graph_alignment=alignment,
        )
        public = {key: value for key, value in change.items() if key != "_files"}
        return {
            "change": public,
            "changed_files": public.get("changed_files") or [],
            "changed_symbols": public.get("changed_symbols") or [],
            "contract_changes": public.get("contract_changes") or [],
            "graph_alignment": alignment,
            "diagnostics": list(config_errors) + list(resolved.get("diagnostics") or []) + list(public.get("diagnostics") or []),
        }

    def explain(self, review_id: str, finding_id: str) -> dict[str, Any]:
        report = read_review(self.settings.output_dir, review_id)
        if report is None:
            return {"code": "review_not_found", "detail": "Unknown review"}
        for finding in report.get("findings") or []:
            if finding.get("finding_id") == finding_id or finding.get("fingerprint") == finding_id:
                return {"review_id": review_id, "finding": finding}
        return {"code": "invalid_finding", "detail": "That finding is not in the stored review."}

    def feedback(self, review_id: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        return record_feedback(self.settings.output_dir, review_id, body)

    def _impact(self, snapshot: Snapshot | None, change: dict[str, Any], as_of: str | None, service_id: str, *, exact: bool) -> dict[str, Any]:
        roots = list(change.get("changed_symbols") or [])
        for contract in change.get("contract_changes") or []:
            contract_id = next((item.get("contract_id") for item in contract.get("consumers") or [] if item.get("contract_id")), "")
            if contract.get("breaking") and contract_id:
                roots.append({
                    "service_id": service_id,
                    "file_path": contract.get("file_path") or "",
                    "line_start": contract.get("line"),
                    "qualified_name": f"{contract.get('method')} {contract.get('path')}".strip(),
                    "key": "",
                    "kind": "contract",
                    "contract_id": contract_id,
                    "topology": "current",
                })
        seen_contracts = {root.get("contract_id") for root in roots if root.get("contract_id")}
        for extra in _handler_contracts(snapshot, change.get("_files") or change.get("changed_files") or []):
            if extra["contract_id"] in seen_contracts:
                continue
            seen_contracts.add(extra["contract_id"])
            roots.append(extra)
        if not roots:
            for item in change.get("_files") or []:
                path = item.get("new_path") or item.get("old_path")
                if not path:
                    continue
                line = None
                hunks = item.get("hunks") or []
                if hunks:
                    lines = hunks[0].get("changed_new_lines") or hunks[0].get("changed_old_lines") or []
                    line = lines[0] if lines else None
                roots.append({"service_id": service_id, "file_path": path, "line_start": line, "qualified_name": path, "key": "", "topology": "previous" if item.get("change_type") == "deleted" else "current"})
        if snapshot is None or (self.cluster is None and not exact):
            return {
                "affected_services": [],
                "ranked_candidates": [],
                "diagnostics": [diagnostic("graph_missing", "No cluster snapshot is available. Structural impact was not calculated.", severity="warning")],
                "temporal_cutoff": as_of,
                "graph_version": "",
            }
        merged: dict[tuple[str, str], dict[str, Any]] = {}
        diagnostics: list[dict] = []
        graph_version = snapshot.version
        limit = int((change.get("budgets") or {}).get("max_nrm_candidates") or 50)
        for root in roots[:limit]:
            if exact or self.cluster is None:
                status, payload = _direct_hybrid(snapshot, root, as_of, self.settings)
            else:
                body = _impact_body(root, service_id, as_of)
                try:
                    status, payload = self.cluster.hybrid_impact(body)
                except Exception as exc:
                    diagnostics.append(diagnostic("graph_missing", str(exc), severity="warning"))
                    continue
            if status != 200:
                diagnostics.append(diagnostic(payload.get("code") or "graph_missing", payload.get("detail") or "Impact did not run.", severity="warning"))
                continue
            graph_version = payload.get("graph_version") or graph_version
            diagnostics.extend(payload.get("diagnostics") or [])
            for candidate in payload.get("candidates") or []:
                _merge_candidate(merged, candidate, root)
        ranked = list(merged.values())
        ranked.sort(key=lambda item: (-(item.get("hybrid_score") or 0), item.get("service_id") or "", item.get("file_path") or ""))
        if snapshot is not None:
            _name_paths(snapshot, ranked)
        for index, item in enumerate(ranked, start=1):
            item["rank"] = index
            origin = item.get("evidence_origin") or "structural_only"
            item["statement"] = ORIGIN_STATEMENT.get(origin, "")
            if origin == "temporal_only":
                item["statement"] = f"{item['statement']} {TEMPORAL_LIMIT}"
            item["score_note"] = SCORE_NOTE
        services = sorted({item.get("service_id") for item in ranked if item.get("service_id")})
        return {
            "affected_services": services,
            "ranked_candidates": ranked[:limit],
            "diagnostics": diagnostics,
            "temporal_cutoff": as_of,
            "graph_version": graph_version,
            "score_note": SCORE_NOTE,
        }

    def _exact(self, review_id: str, repo: Path, resolved: dict[str, Any], service_id: str, snapshot: Snapshot | None, alignment: str) -> tuple[Snapshot | None, str, list[Path], list[dict]]:
        diagnostics: list[dict] = []
        head = resolved.get("head_sha")
        if not head:
            diagnostics.append(diagnostic("git_ref_not_found", "Exact review needs a resolved head revision.", severity="warning"))
            return None, alignment, [], diagnostics
        dest = self.settings.output_dir / "reviews" / review_id / "worktrees" / "head"
        ok, message = materialize_worktree(repo, head, dest)
        if not ok:
            diagnostics.append(diagnostic("git_ref_not_found", message or "The review worktree was not created.", severity="warning"))
            return None, alignment, [], diagnostics
        local, error = _private_snapshot(self.cluster, self.settings, repo, dest, service_id, head, snapshot)
        if local is None:
            diagnostics.append(diagnostic(error or "graph_missing", "Exact graph alignment used the published snapshot because a private partition was not built.", severity="warning"))
            return None, alignment if alignment != "unavailable" else "stale", [dest], diagnostics
        diagnostics.append(diagnostic("exact_partition", "A private review partition was built and was not published as the cluster snapshot.", severity="info"))
        return local, "exact", [dest], diagnostics

    def _model(self, change: dict[str, Any], context: dict[str, Any], policies: list[dict]) -> tuple[list[dict], str, str]:
        provider = self.provider or provider_from_env()
        if isinstance(provider, NoneProvider) or getattr(provider, "name", "none") == "none":
            return [], "", "model_unavailable"
        result = provider.review({"changed_files": change.get("changed_files"), "changed_symbols": change.get("changed_symbols"), "context": context.get("items"), "policies": policies})
        if result.get("diagnostic"):
            return [], "", str(result["diagnostic"])
        return list(result.get("findings") or []), str(result.get("summary") or ""), ""

    def _cleanup(self, repo: Path, worktrees: list[Path]) -> None:
        if retain_worktrees():
            return
        for dest in worktrees:
            remove_worktree(repo, dest)
            if dest.exists():
                shutil.rmtree(dest, ignore_errors=True)

    def _progress(self, review_id: str, stage: str, job_id: str = "") -> None:
        payload = progress_event(stage, review_id, job_id)
        self._event("review_progress", payload)
        callback = getattr(self, "_on_progress", None)
        if callback is not None:
            try:
                callback(int(payload["percent"]))
            except Exception:
                log.info("progress.callback_failed")

    def _event(self, name: str, data: dict) -> None:
        try:
            self.publish(name, data)
        except Exception:
            log.info(name)

    def _finish_incomplete(self, review_id: str, repo: Path, source: dict, errors: list[dict], started: float, timings: dict, resolved: dict | None = None, service_id: str = "", requested_mode: str = "fast") -> dict[str, Any]:
        change = {
            "repository": str(repo),
            "service_id": service_id,
            "base_sha": (resolved or {}).get("base_sha"),
            "head_sha": (resolved or {}).get("head_sha"),
            "source": source,
            "changed_files": [],
            "changed_symbols": [],
            "contract_changes": [],
            "graph_alignment": "unavailable",
            "diagnostics": errors,
        }
        gate = decide_gate([], [], {"gate": {"strict_graph": False}, "review": {}}, alignment="unavailable", diagnostics=errors)
        gate["decision"] = "incomplete"
        gate["evidence_complete"] = False
        timings["total_ms"] = _ms(started)
        report = _report(review_id, change, {"ranked_candidates": [], "affected_services": []}, [], gate, [], {"items": []}, errors, timings, {"config_hash": ""}, "", requested_mode, None, requested_mode=requested_mode, resolved_mode=requested_mode)
        write_review(self.settings.output_dir, review_id, {"manifest.json": _manifest(review_id, report, {}, ""), "report.json": report, "report.md": render_markdown(report)})
        self._event("review_failed", {"review_id": review_id, "decision": "incomplete"})
        return report


def _handler_contracts(snapshot: Snapshot | None, files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rank consumers of a contract implemented in a changed file without calling the contract broken."""
    if snapshot is None:
        return []
    changed = {item.get("new_path") or item.get("old_path") for item in files}
    found = []
    for edge in snapshot.edges:
        if edge.relation != "HANDLED_BY":
            continue
        handler = snapshot.nodes.get(edge.target)
        contract = snapshot.nodes.get(edge.source)
        if handler is None or contract is None or handler.file_path not in changed:
            continue
        identity = str(contract.metadata.get("identity") or "")
        if not identity:
            continue
        found.append({
            "service_id": contract.service_id,
            "file_path": handler.file_path,
            "line_start": handler.line_start,
            "qualified_name": contract.qualified_name,
            "key": "",
            "kind": "contract",
            "contract_id": identity,
            "topology": "current",
        })
    return found


def _name_paths(snapshot: Snapshot, ranked: list[dict[str, Any]]) -> None:
    for item in ranked:
        structural = item.get("structural")
        if not isinstance(structural, dict):
            continue
        named = []
        for step in structural.get("path") or []:
            source = snapshot.nodes.get(step.get("source"))
            target = snapshot.nodes.get(step.get("target"))
            named.append({
                **step,
                "source_name": source.qualified_name if source is not None else step.get("source"),
                "target_name": target.qualified_name if target is not None else step.get("target"),
            })
        structural["path"] = named


def _impact_body(root: dict[str, Any], service_id: str, as_of: str | None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "service": root.get("service_id") or service_id,
        "as_of": as_of,
        "mode": "code_change",
        "topology": root.get("topology") or "current",
    }
    if root.get("contract_id"):
        body["contract_id"] = root["contract_id"]
        return body
    line = None
    changed_lines = root.get("changed_lines") or []
    if changed_lines:
        line = changed_lines[0]
    elif root.get("line_start"):
        line = root.get("line_start")
    if root.get("key") and root.get("kind") not in {"File", "file_anchor"}:
        body["symbol"] = root["key"]
        return body
    body["file"] = root.get("file_path") or ""
    body["line"] = line
    return body


def _direct_hybrid(snapshot: Snapshot, root: dict[str, Any], as_of: str | None, settings: Settings) -> tuple[int, dict[str, Any]]:
    from dep_intel.cluster_service import resolve_roots
    from dep_intel.temporal_providers import provider_for

    body = _impact_body(root, root.get("service_id") or "", as_of)
    roots, _candidates, error = resolve_roots(
        snapshot,
        service=body.get("service") or "",
        symbol=body.get("symbol") or "",
        file=body.get("file") or "",
        line=body.get("line"),
        contract_id=body.get("contract_id") or "",
    )
    if error:
        return 404, {"detail": error, "code": "symbol_not_found"}
    structural = analyze(snapshot, roots, mode="code_change", settings=ImpactSettings())
    try:
        provider = provider_for("commit_history", output_dir=settings.output_dir, service_to_slug={root.get("service_id") or "": root.get("service_id") or ""})
    except ValueError as exc:
        return 400, {"detail": str(exc), "code": "unknown_temporal_provider"}
    payload = analyze_hybrid(snapshot, structural, provider, settings=HybridSettings(), as_of=as_of, provider_name=getattr(provider, "name", "commit_history"))
    return 200, payload


def _private_snapshot(cluster: Any, settings: Settings, repo: Path, worktree: Path, service_id: str, head: str, current: Snapshot | None) -> tuple[Snapshot | None, str]:
    if current is None or cluster is None:
        return None, "graph_missing"
    from dep_intel.config import PROJECT_ROOT

    binary = os.environ.get("CRG_BIN") or ""
    bundled = PROJECT_ROOT / "server" / ".venv" / "bin" / "code-review-graph"
    if not binary and bundled.is_file():
        binary = str(bundled)
    if not binary:
        binary = shutil.which("code-review-graph") or ""
    if not binary:
        return None, "graph_missing"
    completed = subprocess.run([binary, "build", "--repo", str(worktree), "--skip-postprocess", "-q"], capture_output=True, text=True, timeout=120, check=False, shell=False)
    if completed.returncode != 0:
        return None, "graph_missing"
    from dep_intel.crg_adapter import extract_partition
    from dep_intel.cross_repo_graph import assemble
    from dep_intel.sources import read_contract_sources

    db_path = worktree / ".code-review-graph" / "graph.db"
    try:
        partition = extract_partition(db_path, service_id=service_id, repository=str(repo), repo_root=worktree, workspace_head=head)
    except Exception:
        return None, "graph_missing"
    manifest = None
    if hasattr(cluster, "_classify"):
        manifest, _errors = cluster._classify()
    if manifest is None:
        return None, "graph_missing"
    partitions = dict(current.partitions)
    partitions[service_id] = partition
    files: dict[str, dict[str, str]] = {}
    for part in partitions.values():
        root = worktree if part.service_id == service_id else _partition_root(settings, part.repository)
        if root is None or not root.exists():
            files[part.service_id] = {}
            continue
        loaded, _truncated = read_contract_sources(root)
        files[part.service_id] = loaded
    try:
        built = assemble(partitions, manifest, files, version=f"review-{head[:12]}")
    except Exception:
        return None, "graph_missing"
    return built, ""


def _partition_root(settings: Settings, repository: str) -> Path | None:
    if not repository:
        return None
    path = Path(repository)
    if not path.is_absolute():
        path = settings.root / repository
    return path if path.exists() else None


def _merge_candidate(merged: dict[tuple[str, str], dict[str, Any]], candidate: dict[str, Any], root: dict[str, Any]) -> None:
    token = (candidate.get("service_id") or "", candidate.get("file_path") or "")
    structural = (candidate.get("structural") or {}).get("score") or 0
    temporal = (candidate.get("temporal") or {}).get("score") or 0
    hybrid = candidate.get("hybrid_score") or 0
    row = merged.get(token)
    affected = {"service_id": root.get("service_id"), "qualified_name": root.get("qualified_name"), "file_path": root.get("file_path")}
    if row is None:
        merged[token] = {
            **candidate,
            "structural_score": structural,
            "temporal_score": temporal,
            "repository": candidate.get("key", {}).get("repository") if isinstance(candidate.get("key"), dict) else candidate.get("repository") or "",
            "affected_by_roots": [affected],
            "root_service_id": root.get("service_id") or "",
            "paths": [(candidate.get("structural") or {}).get("path") or []],
        }
        return
    row["affected_by_roots"].append(affected)
    if hybrid >= (row.get("hybrid_score") or 0):
        row["hybrid_score"] = hybrid
    if structural >= (row.get("structural_score") or 0):
        row["structural_score"] = structural
        row["structural"] = candidate.get("structural")
        row["paths"] = [((candidate.get("structural") or {}).get("path") or [])] + list(row.get("paths") or [])[:3]


def _report(review_id: str, change: dict, impact: dict, policies: list, gate: dict, verification: list, context: dict, diagnostics: list, timings: dict, config: dict, model_summary: str, mode: str, as_of: str | None, requested_mode: str | None = None, resolved_mode: str | None = None) -> dict[str, Any]:
    files = change.get("changed_files") or []
    summary = {
        "text": _summary_text(change, gate, impact),
        "changed_file_count": len(files),
        "finding_count": len(gate.get("findings") or []),
        "model_summary": model_summary,
    }
    return {
        "schema_version": 1,
        "review_id": review_id,
        "module": MODULE_NAME,
        "module_abbrev": MODULE_ABBREV,
        "module_version": NBG_VERSION,
        "mode": resolved_mode or mode,
        "requested_mode": requested_mode or mode,
        "resolved_mode": resolved_mode or mode,
        "change": {
            "repository": change.get("repository"),
            "service_id": change.get("service_id"),
            "base_sha": change.get("base_sha"),
            "head_sha": change.get("head_sha"),
            "graph_alignment": change.get("graph_alignment"),
            "graph_version": change.get("graph_version") or impact.get("graph_version"),
            "source": change.get("source"),
            "temporal_cutoff": as_of,
        },
        "summary": summary,
        "gate": {key: value for key, value in gate.items() if key != "findings"},
        "changed_files": files,
        "changed_symbols": change.get("changed_symbols") or [],
        "contract_changes": change.get("contract_changes") or [],
        "impact": impact,
        "findings": gate.get("findings") or [],
        "verification": verification,
        "policies": policies,
        "context_bytes": context.get("bytes") or 0,
        "diagnostics": diagnostics,
        "limitations": LIMITATIONS,
        "performance": {key: round(value, 1) for key, value in timings.items()},
        "score_note": SCORE_NOTE,
        "cache": {"hit": False},
    }


def _summary_text(change: dict, gate: dict, impact: dict) -> str:
    files = change.get("changed_files") or []
    names = ", ".join(item.get("new_path") or item.get("old_path") or "" for item in files[:8]) or "no files"
    services = ", ".join((impact.get("affected_services") or [])[:8]) or "none"
    decision = (gate.get("decision") or "incomplete").upper()
    return f"{decision}. Changed {names}. Downstream services in the ranked evidence: {services}. {SCORE_NOTE}"


def _manifest(review_id: str, report: dict, config: dict, mode: str) -> dict[str, Any]:
    change = report.get("change") or {}
    return {
        "review_id": review_id,
        "schema_version": 1,
        "module_version": NBG_VERSION,
        "repository": change.get("repository"),
        "service": change.get("service_id"),
        "base_sha": change.get("base_sha"),
        "head_sha": change.get("head_sha"),
        "graph_version": change.get("graph_version"),
        "graph_alignment": change.get("graph_alignment"),
        "config_hash": config.get("config_hash") or "",
        "mode": mode,
        "temporal_cutoff": change.get("temporal_cutoff"),
        "nrm": {"alpha_structural": 0.70, "beta_temporal": 0.30, "fusion_method": "raw_weighted"},
        "model": {"provider": os.environ.get("NAMI_REVIEW_MODEL_PROVIDER") or "none", "model": os.environ.get("NAMI_REVIEW_MODEL") or ""},
        "started_at": now(),
        "completed_at": now(),
        "status": (report.get("gate") or {}).get("decision"),
    }


def _repository_path(source: dict[str, Any], settings: Settings) -> Path:
    raw = str(source.get("repository") or "")
    path = Path(raw)
    if not path.is_absolute():
        path = settings.root / raw
    return path


def _service_id(snapshot: Snapshot | None, requested: str, repo: Path) -> str:
    if requested:
        return requested
    if snapshot is None:
        return repo.name
    for part in snapshot.partitions.values():
        if part.service_id == repo.name or Path(part.repository).name == repo.name:
            return part.service_id
    return repo.name


def _mark_tested(symbols: list[dict], snapshot: Snapshot | None) -> None:
    if snapshot is None:
        return
    for symbol in symbols:
        symbol["tested_by"] = any(edge.relation == "TESTED_BY" and edge.source == symbol.get("key") for edge in snapshot.edges)


def _cache_key(resolved: dict, config: dict, graph_version: str, mode: str, verify: bool, model_enabled: bool) -> str:
    payload = {
        "base": resolved.get("base_sha"),
        "head": resolved.get("head_sha"),
        "diff": hashlib.sha256((resolved.get("diff_text") or "").encode("utf-8")).hexdigest(),
        "config": config.get("config_hash"),
        "graph": graph_version,
        "mode": mode,
        "verify": verify,
        "model": model_enabled,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:24]


def _read_cache(output_dir: Path, key: str) -> dict | None:
    pointer = read_json(output_dir / "reviews" / "_cache", f"{key}.json")
    if not isinstance(pointer, dict):
        return None
    report = read_review(output_dir, str(pointer.get("review_id") or ""))
    return report


def _write_cache(output_dir: Path, key: str, review_id: str) -> None:
    write_json(output_dir / "reviews" / "_cache", f"{key}.json", {"review_id": review_id, "key": key})


def _secrets(settings: Settings) -> list[str]:
    return [item for item in (settings.github_token, settings.ghe_token, os.environ.get("NAMI_REVIEW_API_KEY", "")) if item]


def _ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000


def public_config(repo: Path | None, settings: Settings) -> dict[str, Any]:
    config, errors = load_review_config(repo, settings.root)
    return {
        "module": MODULE_NAME,
        "module_abbrev": MODULE_ABBREV,
        "module_version": NBG_VERSION,
        "config_hash": config.get("config_hash"),
        "policies": [{"id": item.get("id"), "severity": item.get("severity"), "check": (item.get("check") or {}).get("type")} for item in config.get("policies") or []],
        "gate": config.get("gate"),
        "verification": {"enabled": (config.get("verification") or {}).get("enabled"), "allow_network": (config.get("verification") or {}).get("allow_network"), "commands": [item.get("id") for item in (config.get("verification") or {}).get("commands") or []]},
        "model_configured": model_configured(),
        "diagnostics": errors,
        "score_note": SCORE_NOTE,
    }
