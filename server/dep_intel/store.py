"""JSON output files shared by the API, the dashboard, and MCP clients."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def write_json(directory: Path, name: str, payload: dict[str, Any]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def read_json(directory: Path, name: str) -> Any:
    path = directory / name
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
