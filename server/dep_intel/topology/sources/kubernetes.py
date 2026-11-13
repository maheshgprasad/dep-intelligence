"""Kubernetes Service names are aliases. They are not inferred calls."""

from __future__ import annotations

import re
from pathlib import Path

_KIND = re.compile(r"(?m)^kind:\s*Service\s*$")
_METADATA = re.compile(r"^metadata:\s*$")
_NAME_VALUE = re.compile(r"[A-Za-z0-9._-]+")


def service_aliases(root: Path, service: str) -> list[dict[str, str]]:
    rows = []
    count = 0
    for path in root.rglob("*"):
        if count >= 40 or not path.is_file() or path.suffix.lower() not in {".yml", ".yaml"}:
            continue
        if any(part in {"node_modules", ".git", "dist", "output"} for part in path.relative_to(root).parts):
            continue
        count += 1
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if not _KIND.search(text):
            continue
        alias = _metadata_name(text)
        if alias:
            rows.append({"alias": alias, "service": service, "source": "kubernetes", "file": str(path.relative_to(root))})
    return rows


def _metadata_name(text: str) -> str | None:
    """Read the first metadata name. Line scanning stays linear on hostile input."""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if _METADATA.fullmatch(line) is None:
            continue
        for follower in lines[index + 1:]:
            if not follower.startswith((" ", "\t")):
                break
            stripped = follower.lstrip(" \t")
            if not stripped.startswith("name:"):
                continue
            value = stripped.split(":", 1)[1].strip()
            if _NAME_VALUE.fullmatch(value):
                return value
            return None
    return None
