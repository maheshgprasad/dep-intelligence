"""Evidence Gate. A claim is publishable only when its evidence supports it."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any

from dep_intel.paths import contained
from dep_intel.review_models import diagnostic

log = logging.getLogger("nami.boundary")

_GRADE_ORDER = {"unsupported": 0, "weak": 1, "moderate": 2, "strong": 3, "verified": 4}
_CAUSAL = re.compile(r"\b(will crash|will break|will fail|probability|percent chance|%\s*chance)\b", re.IGNORECASE)
_ALLOWED_SOURCES = {"diff", "CRG", "cluster_graph", "manifest", "OpenAPI", "git_history", "NRM", "test", "verification_command", "policy", "LLM"}


def apply_evidence_gate(
    findings: list[dict[str, Any]],
    change: dict[str, Any],
    repo_root: Path | None,
    *,
    minimum: str = "weak",
    alignment: str = "unavailable",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return publishable findings and rejection diagnostics."""
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    seen: set[str] = set()
    floor = _GRADE_ORDER.get(minimum, 1)
    changed = _changed_index(change)
    for finding in findings:
        reasons = _reject_reasons(finding, change, repo_root, changed, alignment)
        if reasons:
            rejected.append(diagnostic("evidence_rejected", "; ".join(reasons), severity="info", finding_id=finding.get("finding_id"), category=finding.get("category")))
            log.info("evidence.rejected", extra={"reasons": reasons, "category": finding.get("category")})
            continue
        grade = finding.get("evidence_strength") or "weak"
        if alignment == "stale" and grade in {"verified", "strong"} and finding.get("source") in {"NRM", "CRG", "cluster_graph"}:
            finding = {**finding, "evidence_strength": "moderate"}
            finding["evidence"] = [{**item, "stale": True} for item in finding.get("evidence") or []]
            grade = "moderate"
        if _GRADE_ORDER.get(grade, 0) < floor:
            rejected.append(diagnostic("evidence_rejected", f"Evidence grade {grade} is below {minimum}.", severity="info", finding_id=finding.get("finding_id")))
            continue
        fingerprint = finding_fingerprint(finding)
        if fingerprint in seen:
            rejected.append(diagnostic("evidence_rejected", "Duplicate finding.", severity="info", fingerprint=fingerprint))
            continue
        seen.add(fingerprint)
        accepted.append({**finding, "fingerprint": fingerprint})
    return accepted, rejected


def finding_fingerprint(finding: dict[str, Any]) -> str:
    payload = [
        finding.get("category") or "",
        finding.get("policy_id") or "",
        finding.get("service_id") or "",
        finding.get("file_path") or "",
        finding.get("line_start") or "",
        finding.get("category") or "",
    ]
    return hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode("utf-8")).hexdigest()[:20]


def _reject_reasons(finding: dict[str, Any], change: dict[str, Any], repo_root: Path | None, changed: dict[str, dict], alignment: str) -> list[str]:
    reasons: list[str] = []
    path = finding.get("file_path")
    category = finding.get("category") or ""
    claim = finding.get("claim") or ""
    if finding.get("source") == "LLM":
        refs = (finding.get("structured") or {}).get("evidence_refs") or []
        independent = [item for item in finding.get("evidence") or [] if item.get("source") != "LLM"]
        if not refs and not independent:
            reasons.append("An LLM finding has no evidence reference.")
        if _CAUSAL.search(claim):
            reasons.append("An LLM finding states a failure that was not verified.")
    if path:
        safe = _safe(path)
        if safe is None:
            reasons.append("The finding path escapes the repository.")
        elif repo_root is not None and finding.get("source") != "NRM":
            candidate = (repo_root / safe)
            try:
                resolved = candidate.resolve()
            except OSError:
                resolved = candidate
            if not contained(resolved, repo_root.resolve()) or not candidate.exists():
                if safe not in changed and not _deleted(change, safe):
                    reasons.append("The finding file does not exist.")
        if finding.get("line_start") and repo_root is not None and path in changed:
            line_count = _line_count(repo_root, path, changed[path])
            if line_count is not None and int(finding["line_start"]) > line_count:
                reasons.append("The finding line is outside the file.")
        if finding.get("source") == "policy" and path not in changed and category not in {"contract_break", "review_target"}:
            reasons.append("The policy finding does not intersect a changed file.")
    origin = (finding.get("structured") or {}).get("evidence_origin")
    if origin == "temporal_only" and (_CAUSAL.search(claim) or category in {"contract_break", "verification_failure", "security_risk"}):
        reasons.append("Temporal evidence cannot prove a defect or a dependency.")
    if category == "contract_break":
        still_there = _contract_still_present(change, finding)
        if still_there:
            reasons.append("The claimed removed API is still present.")
    if "missing validation" in claim.lower() and path and repo_root is not None:
        text = _read(repo_root, path)
        if text and ("validate(" in text or "validator" in text):
            reasons.append("The source already contains validation.")
    for item in finding.get("evidence") or []:
        if item.get("source") not in _ALLOWED_SOURCES and item.get("source"):
            reasons.append(f"Unknown evidence source {item.get('source')}.")
    if alignment == "unavailable" and finding.get("source") == "NRM" and category == "cross_repo_impact":
        reasons.append("Cross-repository structural evidence is unavailable.")
    return reasons


def _changed_index(change: dict[str, Any]) -> dict[str, dict]:
    index = {}
    for item in change.get("_files") or change.get("changed_files") or []:
        path = item.get("new_path") or item.get("old_path")
        if path:
            index[path] = item
    return index


def _deleted(change: dict[str, Any], path: str) -> bool:
    for item in change.get("changed_files") or []:
        if item.get("change_type") == "deleted" and item.get("old_path") == path:
            return True
    return False


def _contract_still_present(change: dict[str, Any], finding: dict[str, Any]) -> bool:
    contract = (finding.get("structured") or {}).get("contract") or {}
    if contract.get("kind") != "operation_removed":
        return False
    for item in change.get("contract_changes") or []:
        if item.get("kind") == "operation_removed" and item.get("path") == contract.get("path") and item.get("method") == contract.get("method"):
            return False
    return bool(contract)


def _line_count(repo: Path, path: str, changed: dict[str, Any]) -> int | None:
    text = _read(repo, path)
    if text:
        return len(text.splitlines()) or 1
    hunks = changed.get("hunks") or []
    if not hunks:
        return None
    return max((hunk.get("new_start") or 0) + (hunk.get("new_count") or 0) for hunk in hunks)


def _read(repo: Path, path: str) -> str:
    safe = _safe(path)
    if safe is None:
        return ""
    candidate = (repo / safe).resolve()
    if not contained(candidate, repo.resolve()) or not candidate.is_file():
        return ""
    from dep_intel.review_sources import read_utf8

    return read_utf8(candidate)


def _safe(path: str) -> str | None:
    if path.startswith("/") or ".." in Path(path).parts:
        return None
    return path
