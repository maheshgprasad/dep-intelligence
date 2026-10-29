"""Assemble and store an immutable intelligence report from a published snapshot."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from dep_intel.config import Settings
from dep_intel.graph_models import Snapshot
from dep_intel.reporting.models import NAMI_TRACE_VERSION, SCHEMA, SCHEMA_VERSION
from dep_intel.reporting.scopes.cluster import build_cluster_document
from dep_intel.reporting.scopes.repository import build_repository_document
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
) -> dict[str, Any]:
    if snapshot is None or not snapshot.version:
        raise ReportError("No published intelligence is available. Refresh intelligence before generating a report.")
    if graph_version and graph_version != snapshot.version:
        raise ReportError(
            f"Published intelligence is {snapshot.version}, not {graph_version}. "
            "The report was not generated from a different snapshot."
        )
    if scope == "cluster":
        body = build_cluster_document(settings, snapshot)
    elif scope == "repository":
        if not service:
            raise ReportError("A repository report needs a service.")
        if service not in snapshot.partitions:
            raise ReportError(f"Unknown service {service}.")
        body = build_repository_document(settings, snapshot, service)
    else:
        raise ReportError("Scope must be cluster or repository.")
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
    manifest = {
        "report_id": report_id,
        "scope": scope,
        "service_id": service or None,
        "graph_version": snapshot.version,
        "created_at": generated_at,
        "nami_version": NAMI_TRACE_VERSION,
        "report_schema_version": SCHEMA_VERSION,
    }
    save_report(settings.output_dir, document, manifest)
    log.info(
        "report.generated",
        extra={"report_id": report_id, "scope": scope, "service": service or None, "graph_version": snapshot.version},
    )
    return document
