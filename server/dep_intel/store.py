"""JSON output files shared by the API, the dashboard, and MCP clients."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def write_json(directory: Path, name: str, payload: dict[str, Any], *, indent: int | None = 2) -> Path:
    """Atomically replace one JSON file in the same directory."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    fd, temporary = tempfile.mkstemp(dir=directory, prefix=f".{name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=indent, default=str)
            if indent is not None:
                handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise
    return path


def read_json(directory: Path, name: str) -> Any:
    path = directory / name
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
