"""Explicit review feedback. It never rewrites NRM weights, history, or labels."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from dep_intel.review_models import diagnostic
from dep_intel.review_store import read_review, review_dir
from dep_intel.store import now, read_json, write_json

STATUSES = {"accepted", "rejected", "false_positive", "fixed", "duplicate", "not_applicable", "acknowledged_risk"}
_ALIASES = {
    "useful": "accepted",
    "false_positive": "false_positive",
    "not_applicable": "not_applicable",
    "duplicate": "duplicate",
    "fixed": "fixed",
    "accepted_risk": "acknowledged_risk",
    "acknowledged_risk": "acknowledged_risk",
}


def record_feedback(output_dir: Path, review_id: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    report = read_review(output_dir, review_id)
    if report is None:
        return 404, {"detail": "Unknown review", "code": "review_not_found"}
    status = _ALIASES.get(str(body.get("status") or ""), str(body.get("status") or ""))
    if status not in STATUSES:
        return 400, {"detail": "Unknown feedback status", "code": "invalid_feedback"}
    fingerprint = str(body.get("finding_fingerprint") or "")
    known = {item.get("fingerprint") for item in report.get("findings") or []}
    if fingerprint not in known:
        return 400, {"detail": "The finding fingerprint is not in this review", "code": "invalid_finding"}
    entry = {
        "finding_fingerprint": fingerprint,
        "review_id": review_id,
        "status": status,
        "scope": body.get("scope") or {},
        "comment": body.get("comment") or "",
        "created_at": now(),
        "effect": "presentation_only",
        "nrm_weights_modified": False,
    }
    directory = review_dir(output_dir, review_id)
    current = read_json(directory, "feedback.json")
    rows = list(current.get("items")) if isinstance(current, dict) else []
    rows.append(entry)
    write_json(directory, "feedback.json", {"items": rows, "note": "Feedback does not modify NRM scores, weights, history, or evaluation labels."})
    global_path = output_dir / "reviews"
    existing = read_json(global_path, "feedback.json")
    all_rows = list(existing.get("items")) if isinstance(existing, dict) else []
    all_rows.append(entry)
    write_json(global_path, "feedback.json", {"items": all_rows})
    repeated = [item for item in all_rows if item.get("finding_fingerprint") == fingerprint and item.get("status") in {"rejected", "false_positive"}]
    suggestion = None
    if len(repeated) >= 3:
        suggestion = {
            "action": "Promote feedback to policy",
            "message": "This finding was rejected more than once. Nami can draft a suppression policy only after explicit approval.",
            "applied": False,
        }
    return 200, {"stored": entry, "suggestion": suggestion, "diagnostics": [] if suggestion is None else [diagnostic("feedback_suggestion", suggestion["message"], severity="info")]}
