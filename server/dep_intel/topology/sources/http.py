"""HTTP servers and clients. A path without a host is not a provider identity."""

from __future__ import annotations

import re
from pathlib import Path

_ROUTE = re.compile(r"""(?:@[\w.]+\.|\.)(get|post|put|patch|delete|head|options)\(\s*["'](/[^"']*)["']""", re.IGNORECASE)
_CLIENT = re.compile(r"""(?:axios|fetch|httpx|requests|http|https)\.(?:get|post|put|patch|delete|request)\(\s*["']([^"']+)["']""", re.IGNORECASE)
_SERVICES = re.compile(r"""SERVICES\s*=\s*\(([^)]{0,500})\)""")
_URL = re.compile(r"""["'](https?://[^"'\s]+)["']""")
_SKIP = {"node_modules", ".git", "dist", "output", ".venv", "coverage"}


def scan(root: Path) -> dict[str, list[dict[str, str]]]:
    routes: list[dict[str, str]] = []
    calls: list[dict[str, str]] = []
    notes: list[str] = []
    seen = 0
    for path in root.rglob("*"):
        if seen >= 250:
            break
        if not path.is_file() or path.suffix.lower() not in {".js", ".ts", ".py", ".go", ".yml", ".yaml", ".env"}:
            continue
        relative_parts = path.relative_to(root).parts
        if any(part in _SKIP for part in relative_parts):
            continue
        seen += 1
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        relative = str(path.relative_to(root))
        for method, route in _ROUTE.findall(text):
            routes.append({"method": method.upper(), "path": route, "file": relative})
        for target in _CLIENT.findall(text):
            calls.append({"method": "", "url": target, "file": relative})
        if _SERVICES.search(text):
            notes.append(f"{relative} builds http://{{service}} from a service list. Nami does not turn those names into providers.")
        if path.name == ".env" or path.suffix == ".env":
            for match in _URL.findall(text):
                calls.append({"method": "", "url": match, "file": relative})
    return {"routes": routes[:80], "calls": calls[:80], "notes": notes[:20]}
