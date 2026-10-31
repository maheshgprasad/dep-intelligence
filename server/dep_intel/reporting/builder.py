"""Assemble and store an immutable intelligence report from a published snapshot."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from dep_intel.config import Settings
from dep_intel.graph_models import Snapshot
from dep_intel.reporting.models import NAMI_TRACE_VERSION, SCHEMA, SCHEMA_VERSION
from dep_intel.reporting.scopes.repository import build_repository_document
from dep_intel.reporting.scopes.service_cluster import build_service_cluster_document
from dep_intel.reporting.scopes.workspace import build_workspace_document
from dep_intel.reporting.store import fingerprint, new_report_id, save_report

log = logging.getLogger("nami.report")


class ReportError(ValueError):
    """The requested point-in-time intelligence cannot be reported."""


def generate_report(
    settings: Settings,
    snapshot: Snapshot | None,
    scope: str,
    service: str = "",
    graph_version: str = "",
    cluster_id: str = "",
) -> dict[str, Any]:
    if snapshot is None or not snapshot.version:
        raise ReportError("No published intelligence is available. Refresh intelligence before generating a report.")
    if graph_version and graph_version != snapshot.version:
        raise ReportError(
            f"Published intelligence is {snapshot.version}, not {graph_version}. "
            "The report was not generated from a different snapshot."
        )
    requested = scope
    if scope == "cluster":
        scope = "workspace"
    if scope == "workspace":
        body = build_workspace_document(settings, snapshot)
    elif scope == "service_cluster":
        if not cluster_id:
            raise ReportError("A service cluster report needs a cluster id.")
        try:
            body = build_service_cluster_document(settings, snapshot, cluster_id)
        except KeyError as exc:
            raise ReportError(f"Unknown service cluster {cluster_id}.") from exc
    elif scope == "repository":
        if not service:
            raise ReportError("A repository report needs a service.")
        if service not in snapshot.partitions:
            raise ReportError(f"Unknown service {service}.")
        body = build_repository_document(settings, snapshot, service)
    else:
        raise ReportError("Scope must be workspace, service_cluster, repository, or the legacy alias cluster.")
    body["requested_scope"] = requested
    report_id = new_report_id()
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    document = {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "report_id": report_id,
        "generated_at": generated_at,
        **body,
        "reproducibility": {
            "nami_trace_version": NAMI_TRACE_VERSION,
            "report_schema_version": SCHEMA_VERSION,
            "report_id": report_id,
            "generated_at": generated_at,
            "graph_version": snapshot.version,
            "note": "Integrity fingerprint; not a cryptographic signature.",
        },
    }
    document["reproducibility"]["report_hash"] = fingerprint(document)
    identity = body.get("identity") or {}
    if body["scope"] == "repository":
        manifest_service = service or identity.get("service") or None
        manifest_cluster = None
    elif body["scope"] == "service_cluster":
        manifest_service = None
        manifest_cluster = identity.get("cluster_id") or cluster_id or None
    else:
        manifest_service = None
        manifest_cluster = None
    manifest = {
        "report_id": report_id,
        "scope": body["scope"],
        "service_id": manifest_service,
        "cluster_id": manifest_cluster,
        "label": identity.get("cluster_label") or "",
        "requested_scope": requested,
        "graph_version": snapshot.version,
        "created_at": generated_at,
        "readiness": (document.get("executive_summary") or {}).get("readiness") or "",
        "readiness_label": (document.get("executive_summary") or {}).get("readiness_label") or "",
        "nami_version": NAMI_TRACE_VERSION,
        "report_schema_version": SCHEMA_VERSION,
    }
    save_report(settings.output_dir, document, manifest)
    log.info(
        "report.generated",
        extra={"report_id": report_id, "scope": scope, "service": service or None, "graph_version": snapshot.version},
    )
    return document
