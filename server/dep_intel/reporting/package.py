"""Cached exports rendered from a stored intelligence report."""

from __future__ import annotations

import io
import json
import logging
import zipfile
from pathlib import Path
from typing import Any

from dep_intel.reporting.render_markdown import render_markdown
from dep_intel.reporting.render_pdf import render_report_pdf
from dep_intel.reporting.store import export_filename, read_manifest, read_report, report_dir, safe_service_name
from dep_intel.review_export import _scrub
from dep_intel.store import read_json, write_json

log = logging.getLogger("nami.report")
EXPORT_RENDERER = "stored-1"


def ensure_export(output_dir: Path, report_id: str, kind: str) -> Path:
    document = read_report(output_dir, report_id)
    if document is None:
        raise FileNotFoundError(report_id)
    names = {"json": "report.json", "pdf": "report.pdf", "markdown": "report.md", "zip": "evidence.zip"}
    if kind not in names:
        raise ValueError(kind)
    token = f"{document.get('reproducibility', {}).get('report_hash')}|{EXPORT_RENDERER}"
    cache = report_dir(output_dir, report_id) / "exports"
    cache.mkdir(parents=True, exist_ok=True)
    current = read_json(cache, "cache.json")
    target = cache / names[kind]
    if isinstance(current, dict) and current.get("token") == token and target.is_file():
        return target
    payload = _scrub(document)
    (cache / "report.json").write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    (cache / "report.md").write_text(render_markdown(payload), encoding="utf-8")
    (cache / "report.pdf").write_bytes(render_report_pdf(payload))
    (cache / "evidence.zip").write_bytes(build_zip(payload, read_manifest(output_dir, report_id)))
    write_json(cache, "cache.json", {"token": token})
    log.info(
        "report.exported",
        extra={
            "report_id": report_id,
            "scope": document.get("scope"),
            "service": (document.get("identity") or {}).get("service"),
            "graph_version": (document.get("reproducibility") or {}).get("graph_version"),
        },
    )
    return target


def public_filename(output_dir: Path, report_id: str, kind: str) -> str:
    document = read_report(output_dir, report_id)
    if document is None:
        raise FileNotFoundError(report_id)
    return export_filename(document, kind)


def build_zip(document: dict[str, Any], manifest: dict[str, Any]) -> bytes:
    scope = document.get("scope")
    report_id = str(document.get("report_id") or "report")
    if scope in {"cluster", "workspace"}:
        root = f"nami-workspace-report-{report_id}"
    elif scope == "service_cluster":
        root = f"nami-service-cluster-report-{report_id}"
    else:
        service = safe_service_name((document.get("identity") or {}).get("service"))
        root = f"nami-repository-{service}-{report_id}"
    files: dict[str, bytes] = {}
    files[f"{root}/report.json"] = _bytes(document)
    files[f"{root}/report.md"] = render_markdown(document).encode("utf-8")
    files[f"{root}/report.pdf"] = render_report_pdf(document)
    if scope == "repository":
        files[f"{root}/architecture/summary.json"] = _bytes(document.get("architecture"))
        files[f"{root}/architecture/relationships.json"] = _bytes(document.get("relationships"))
        files[f"{root}/contracts/provided.json"] = _bytes((document.get("contracts") or {}).get("provided"))
        files[f"{root}/contracts/consumed.json"] = _bytes((document.get("contracts") or {}).get("consumed"))
        files[f"{root}/topology/service.json"] = _bytes(document.get("service"))
        files[f"{root}/topology/relationships.json"] = _bytes(document.get("relationships"))
        files[f"{root}/topology/unresolved.json"] = _bytes(document.get("intelligence_gaps"))
        files[f"{root}/history/summary.json"] = _bytes(document.get("history"))
        files[f"{root}/health/packages.json"] = _bytes((document.get("health") or {}).get("packages"))
        files[f"{root}/health/security.json"] = _bytes((document.get("health") or {}).get("security"))
        files[f"{root}/health/coverage.json"] = _bytes((document.get("health") or {}).get("coverage"))
        files[f"{root}/reviews/summary.json"] = _bytes(document.get("review_activity"))
    else:
        files[f"{root}/cluster/services.json"] = _bytes(document.get("services"))
        files[f"{root}/cluster/relationships.json"] = _bytes(document.get("relationships"))
        files[f"{root}/cluster/contracts.json"] = _bytes(document.get("contracts"))
        files[f"{root}/topology/relationships.json"] = _bytes(document.get("relationships"))
        files[f"{root}/topology/unresolved.json"] = _bytes(document.get("unresolved"))
        files[f"{root}/topology/provenance.json"] = _bytes((document.get("relationships") or {}).get("provenance"))
        files[f"{root}/history/summary.json"] = _bytes(document.get("history"))
        files[f"{root}/health/package-summary.json"] = _bytes((document.get("health") or {}).get("packages"))
        files[f"{root}/health/security-summary.json"] = _bytes((document.get("health") or {}).get("security"))
        files[f"{root}/health/coverage-summary.json"] = _bytes((document.get("health") or {}).get("coverage"))
        files[f"{root}/reviews/summary.json"] = _bytes(document.get("review_activity"))
        for service in document.get("services") or []:
            name = safe_service_name(service.get("service"))
            files[f"{root}/repositories/{name}.json"] = _bytes(service)
    files[f"{root}/metadata/reproducibility.json"] = _bytes(document.get("reproducibility"))
    listed = sorted(files)
    files[f"{root}/metadata/manifest.json"] = _bytes({"report_id": report_id, "files": [*listed, f"{root}/metadata/manifest.json"]})
    blob = io.BytesIO()
    with zipfile.ZipFile(blob, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(files):
            if _forbidden(name):
                continue
            archive.writestr(name, files[name])
    return blob.getvalue()


def _bytes(value: Any) -> bytes:
    return json.dumps(_scrub(value), indent=2, sort_keys=True, default=str).encode("utf-8")


def _forbidden(name: str) -> bool:
    lowered = name.lower()
    return any(token in lowered for token in (".env", "/.git/", "worktree", ".git/"))
