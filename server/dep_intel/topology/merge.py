"""Combine discovered observations with optional overrides and the compatibility manifest."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from dep_intel.topology.resolver import resolve


def effective(discovered: dict[str, Any], root: Path, bindings: list[dict]) -> dict[str, Any]:
    aliases = list(discovered.get("aliases") or [])
    overrides = _overrides(root / "nami-topology.yaml")
    for alias, service in overrides.items():
        aliases.append({"alias": alias, "service": service, "source": "nami-topology.yaml"})
    resolved = resolve(discovered.get("services") or [], discovered.get("calls") or [], aliases, bindings)
    return {
        "schema": "nami.topology.effective",
        "schema_version": 1,
        "services": discovered.get("services") or [],
        "relationships": resolved["relationships"],
        "unresolved": resolved["unresolved"],
        "overrides": [{"alias": key, "service": value} for key, value in sorted(overrides.items())],
        "provided_operations": discovered.get("operations") or [],
        "note": "Route equality alone does not create a relationship. Classic cluster-manifest.json remains a compatibility input.",
    }


def _overrides(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    aliases = document.get("aliases") if isinstance(document, dict) else None
    if not isinstance(aliases, dict):
        return {}
    result = {}
    for key, value in aliases.items():
        if isinstance(value, dict) and value.get("service"):
            result[str(key)] = str(value["service"])
    return result
