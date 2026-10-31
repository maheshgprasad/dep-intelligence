"""Canonical intelligence-report constants.

Review exports keep the separate schema nami.review.export.
"""

from __future__ import annotations

SCHEMA = "nami.intelligence.report"
SCHEMA_VERSION = 1
NAMI_TRACE_VERSION = "0.1.0"

STATUS_LABELS = {
    "current": "Current",
    "available": "Available",
    "partial": "Partial",
    "stale": "Out of date",
    "unavailable": "Unavailable",
    "not_scanned": "Not scanned",
    "missing": "Unavailable",
    "failed": "Unavailable",
    "limited": "Limited",
    "complete": "Complete",
    "none": "No findings",
    "workspace_only": "Workspace-wide",
}
