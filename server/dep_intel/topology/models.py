"""Topology records. These are observations, not Gate decisions."""

from __future__ import annotations

from typing import Any

SCHEMA = "nami.topology"
SCHEMA_VERSION = 1


def relationship(source: str, target: str, method: str, path: str, reasons: list[str], origin: str) -> dict[str, Any]:
    return {
        "from": source,
        "to": target,
        "method": method,
        "path": path,
        "reasons": reasons,
        "origin": origin,
    }


def unresolved(source: str, method: str, path: str, host: str, message: str, snippet: str) -> dict[str, Any]:
    return {
        "from": source,
        "method": method,
        "path": path,
        "host": host,
        "message": message,
        "override_snippet": snippet,
    }
