"""Kubernetes Service names are aliases. They are not inferred calls."""

from __future__ import annotations

import re
from pathlib import Path

_KIND = re.compile(r"(?m)^kind:\s*Service\s*$")
_NAME = re.compile(r"(?m)^metadata:\s*\n(?:[ \t]+.+\n)*?[ \t]+name:\s*([A-Za-z0-9._-]+)")


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
        match = _NAME.search(text)
        if match:
            rows.append({"alias": match.group(1), "service": service, "source": "kubernetes", "file": str(path.relative_to(root))})
    return rows
