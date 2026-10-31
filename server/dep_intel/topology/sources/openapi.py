"""OpenAPI operations are provided contracts. They do not identify a caller."""

from __future__ import annotations

from pathlib import Path

from dep_intel.contract_extractors.openapi import parse_openapi

_NAMES = {"openapi.yaml", "openapi.yml", "openapi.json", "swagger.json", "swagger.yaml", "swagger.yml"}


def operations(root: Path) -> list[dict[str, str]]:
    rows = []
    for path in _named(root):
        try:
            document = parse_openapi(path.read_text(encoding="utf-8", errors="replace"), str(path))
        except (OSError, ValueError):
            continue
        for operation in document.operations:
            rows.append({"method": operation.method, "path": operation.path, "file": str(path.relative_to(root))})
    return rows


def _named(root: Path) -> list[Path]:
    found = []
    for path in root.rglob("*"):
        if not path.is_file() or path.name.lower() not in _NAMES:
            continue
        if _skipped(path, root):
            continue
        found.append(path)
        if len(found) >= 8:
            break
    return found


def _skipped(path: Path, root: Path) -> bool:
    return any(part in {"node_modules", ".git", "dist", "output"} for part in path.relative_to(root).parts)
