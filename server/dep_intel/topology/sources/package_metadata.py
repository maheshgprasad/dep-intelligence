"""Package names that can act as service aliases. They do not create calls."""

from __future__ import annotations

import json
import re
from pathlib import Path

_MODULE = re.compile(r"^module\s+(\S+)", re.MULTILINE)
_PY_NAME = re.compile(r"^name\s*=\s*[\"']([^\"']+)[\"']", re.MULTILINE)


def aliases_from_packages(root: Path, service: str) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    package = root / "package.json"
    if package.is_file():
        try:
            name = str(json.loads(package.read_text(encoding="utf-8")).get("name") or "")
        except (OSError, json.JSONDecodeError):
            name = ""
        if name:
            found.append({"alias": name, "service": service, "source": "package.json"})
    gomod = root / "go.mod"
    if gomod.is_file():
        match = _MODULE.search(_read(gomod))
        if match:
            found.append({"alias": match.group(1).rsplit("/", 1)[-1], "service": service, "source": "go.mod"})
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        match = _PY_NAME.search(_read(pyproject))
        if match:
            found.append({"alias": match.group(1), "service": service, "source": "pyproject.toml"})
    return found


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
