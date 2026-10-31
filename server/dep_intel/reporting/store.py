"""Immutable intelligence reports under output/reports/<id>/."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from pathlib import Path
from typing import Any

from dep_intel.store import read_json, write_json

_SAFE = re.compile(r"[^a-z0-9]+")


def new_report_id() -> str:
    return f"rpt-{uuid.uuid4().hex}"


def report_dir(output_dir: Path, report_id: str) -> Path:
    return output_dir / "reports" / report_id


def fingerprint(document: dict[str, Any]) -> str:
    """SHA-256 of canonical JSON. This is an integrity fingerprint, not a signature."""
    body = json.loads(json.dumps(document, sort_keys=True, default=str))
    reproducibility = dict(body.get("reproducibility") or {})
    reproducibility.pop("report_hash", None)
    body["reproducibility"] = reproducibility
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def save_report(output_dir: Path, document: dict[str, Any], manifest: dict[str, Any]) -> Path:
    directory = report_dir(output_dir, document["report_id"])
    directory.mkdir(parents=True, exist_ok=True)
    write_json(directory, "report.json", document)
    write_json(directory, "manifest.json", manifest)
    return directory


def read_report(output_dir: Path, report_id: str) -> dict[str, Any] | None:
    payload = read_json(report_dir(output_dir, report_id), "report.json")
    if not isinstance(payload, dict) or payload.get("schema") != "nami.intelligence.report":
        return None
    return payload


def read_manifest(output_dir: Path, report_id: str) -> dict[str, Any]:
    payload = read_json(report_dir(output_dir, report_id), "manifest.json")
    return payload if isinstance(payload, dict) else {}


def list_reports(output_dir: Path, limit: int = 20) -> list[dict[str, Any]]:
    """Newest reports, metadata only. A corrupt directory is skipped."""
    root = output_dir / "reports"
    if not root.is_dir():
        return []
    rows = []
    for directory in root.iterdir():
        if not directory.is_dir() or directory.name.startswith("_"):
            continue
        try:
            manifest = read_json(directory, "manifest.json")
            if not isinstance(manifest, dict) or not manifest.get("report_id"):
                continue
            scope = str(manifest.get("scope") or "")
            service_id = manifest.get("service_id")
            cluster_id = manifest.get("cluster_id")
            if scope == "service_cluster" and not cluster_id and isinstance(service_id, str) and service_id.startswith("svc-cluster-"):
                cluster_id = service_id
            rows.append({
                "report_id": str(manifest.get("report_id")),
                "scope": scope,
                "service": service_id,
                "cluster_id": cluster_id,
                "label": manifest.get("label") or "",
                "generated_at": manifest.get("created_at") or "",
                "graph_version": manifest.get("graph_version") or "",
                "readiness": manifest.get("readiness") or "",
                "readiness_label": manifest.get("readiness_label") or "",
                "_mtime": directory.stat().st_mtime,
            })
        except (OSError, TypeError, ValueError):
            continue
    rows.sort(key=lambda item: (item.get("generated_at") or "", item.get("_mtime") or 0), reverse=True)
    bounded = max(1, min(int(limit or 20), 100))
    cleaned = []
    for row in rows[:bounded]:
        row.pop("_mtime", None)
        cleaned.append(row)
    return cleaned


def safe_service_name(service: str | None) -> str:
    text = _SAFE.sub("-", (service or "repository").lower()).strip("-")
    return text[:40] or "repository"


def export_filename(document: dict[str, Any], kind: str) -> str:
    short = str(document.get("report_id") or "")[:12].replace("rpt-", "")[:8]
    scope = document.get("scope")
    if scope in {"cluster", "workspace"}:
        stem = f"nami-workspace-report-{short}"
    elif scope == "service_cluster":
        stem = f"nami-service-cluster-report-{short}"
    else:
        service = safe_service_name((document.get("identity") or {}).get("service"))
        stem = f"nami-{service}-report-{short}"
    suffix = {"pdf": ".pdf", "markdown": ".md", "json": ".json", "zip": "-evidence.zip"}[kind]
    return stem + suffix
