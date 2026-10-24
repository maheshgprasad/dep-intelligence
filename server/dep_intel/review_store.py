"""Persist one Boundary Gate run under output/reviews/<review-id>/."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

from dep_intel.store import read_json, write_json


def review_dir(output_dir: Path, review_id: str) -> Path:
    return output_dir / "reviews" / review_id


def write_review(output_dir: Path, review_id: str, artifacts: dict[str, Any]) -> Path:
    directory = review_dir(output_dir, review_id)
    directory.mkdir(parents=True, exist_ok=True)
    for name, payload in artifacts.items():
        if name.endswith(".md") and isinstance(payload, str):
            _write_text(directory, name, payload)
        elif isinstance(payload, dict):
            write_json(directory, name, payload)
    return directory


def list_reviews(output_dir: Path, limit: int = 20) -> list[dict[str, Any]]:
    """Newest stored reviews, metadata only. A corrupt directory is skipped."""
    root = output_dir / "reviews"
    if not root.is_dir():
        return []
    rows = []
    for directory in root.iterdir():
        if not directory.is_dir() or directory.name.startswith("_"):
            continue
        try:
            report = read_json(directory, "report.json")
            manifest = read_json(directory, "manifest.json")
            if not isinstance(report, dict) and not isinstance(manifest, dict):
                continue
            report = report if isinstance(report, dict) else {}
            manifest = manifest if isinstance(manifest, dict) else {}
            change = report.get("change") if isinstance(report.get("change"), dict) else {}
            gate = report.get("gate") if isinstance(report.get("gate"), dict) else {}
            source = change.get("source") if isinstance(change.get("source"), dict) else {}
            findings = report.get("findings") if isinstance(report.get("findings"), list) else []
            rows.append({
                "review_id": str(manifest.get("review_id") or report.get("review_id") or directory.name),
                "decision": gate.get("decision") or manifest.get("status") or "",
                "repository": change.get("repository") or manifest.get("repository") or "",
                "service": change.get("service_id") or manifest.get("service") or "",
                "source_kind": source.get("kind") or "",
                "base_sha": change.get("base_sha") or manifest.get("base_sha") or "",
                "head_sha": change.get("head_sha") or manifest.get("head_sha") or "",
                "completed_at": manifest.get("completed_at") or "",
                "finding_count": len(findings),
                "_mtime": directory.stat().st_mtime,
            })
        except (OSError, TypeError, ValueError):
            continue
    rows.sort(key=lambda item: item.get("_mtime") or 0, reverse=True)
    bounded = max(1, min(int(limit or 20), 100))
    cleaned = []
    for row in rows[:bounded]:
        row.pop("_mtime", None)
        cleaned.append(row)
    return cleaned


def read_review(output_dir: Path, review_id: str) -> dict[str, Any] | None:
    payload = read_json(review_dir(output_dir, review_id), "report.json")
    if not isinstance(payload, dict):
        return None
    return payload


def render_markdown(report: dict[str, Any]) -> str:
    gate = report.get("gate") or {}
    change = report.get("change") or {}
    lines = [
        "# Nami Boundary Gate Review",
        "",
        f"Decision: {(gate.get('decision') or 'incomplete').upper()}",
        "",
        f"Repository: {change.get('repository') or report.get('repository') or ''}",
        f"Base: {change.get('base_sha') or ''}",
        f"Head: {change.get('head_sha') or ''}",
        "",
        "## Change summary",
        "",
        report.get("summary", {}).get("text") or "No summary was recorded.",
        "",
        "## Cross-repository impact",
        "",
    ]
    candidates = (report.get("impact") or {}).get("ranked_candidates") or []
    if not candidates:
        lines.append("No ranked downstream candidate was produced.")
    for item in candidates[:20]:
        lines.append(
            f"- {item.get('service_id')}:{item.get('file_path')} origin={item.get('evidence_origin')} "
            f"structural={item.get('structural_score')} temporal={item.get('temporal_score')} nrm={item.get('hybrid_score')}"
        )
        if item.get("statement"):
            lines.append(f"  {item['statement']}")
    lines.extend(["", "## Blocking findings", ""])
    lines.extend(_finding_lines(report, gate.get("blocking_findings") or []))
    lines.extend(["", "## Warnings", ""])
    lines.extend(_finding_lines(report, gate.get("warning_findings") or []))
    lines.extend(["", "## Review targets", ""])
    lines.extend(_finding_lines(report, gate.get("review_targets") or []))
    lines.extend(["", "## Verification", ""])
    verification = report.get("verification") or []
    if not verification:
        lines.append("No verification command ran.")
    for item in verification:
        lines.append(f"- {item.get('id')}: {item.get('status')} exit={item.get('exit_code')} duration_ms={item.get('duration_ms')}")
    lines.extend(["", "## Applied policies", ""])
    for item in report.get("policies") or []:
        lines.append(f"- {item.get('policy_id')}: applied={item.get('applied')} result={item.get('result')} — {item.get('evidence')}")
    lines.extend(["", "## Evidence limitations", ""])
    for item in report.get("limitations") or []:
        lines.append(f"- {item}")
    lines.extend(["", "## Diagnostics", ""])
    for item in report.get("diagnostics") or []:
        lines.append(f"- {item.get('code')}: {item.get('message')}")
    lines.append("")
    return "\n".join(lines)


def _finding_lines(report: dict[str, Any], identifiers: list[str]) -> list[str]:
    if not identifiers:
        return ["None."]
    by_id = {item.get("finding_id"): item for item in report.get("findings") or []}
    rows = []
    for ident in identifiers:
        item = by_id.get(ident) or {}
        rows.append(
            f"- [{item.get('severity')}/{item.get('evidence_strength')}] {item.get('title') or item.get('claim')} "
            f"({item.get('file_path') or 'no file'}:{item.get('line_start') or ''})"
        )
    return rows


def _write_text(directory: Path, name: str, text: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=directory, prefix=f".{name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, directory / name)
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise
