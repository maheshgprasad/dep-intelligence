"""Label code-review-graph flow node ids with repository-relative files.

``list_flows`` stores ``path`` as database ids. The dashboard reads those ids
back out of the graph and shows the file and symbol instead.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

_FILE_KEYS = {"file", "file_path", "relative_path"}
_cache: dict[tuple[str, int], dict[int, str]] = {}


def relative_repo_path(path: str, root: str) -> str:
    text = path.replace("\\", "/")
    base = root.replace("\\", "/").rstrip("/")
    if base and (text == base or text.startswith(base + "/")):
        return text[len(base) :].lstrip("/")
    return text


def node_label(name: str, file_path: str, root: str) -> str:
    relative = relative_repo_path(file_path, root) or file_path
    symbol = name.strip()
    if symbol and symbol not in relative:
        return f"{relative} ({symbol})"
    return relative


def load_node_labels(db_path: Path, root: str) -> dict[int, str]:
    if not db_path.is_file():
        return {}
    key = (str(db_path), db_path.stat().st_mtime_ns)
    cached = _cache.get(key)
    if cached is not None:
        return cached
    labels: dict[int, str] = {}
    try:
        connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            rows = connection.execute("SELECT id, name, file_path FROM nodes").fetchall()
        finally:
            connection.close()
    except sqlite3.Error:
        return {}
    for node_id, name, file_path in rows:
        if file_path:
            labels[int(node_id)] = node_label(str(name or ""), str(file_path), root)
    _cache.clear()
    _cache[key] = labels
    return labels


def annotate_flow_paths(value: Any, labels: dict[int, str], root: str) -> None:
    """Add ``path_files`` and rewrite absolute file fields under ``root``."""
    if isinstance(value, list):
        for item in value:
            annotate_flow_paths(item, labels, root)
        return
    if not isinstance(value, dict):
        return
    path = value.get("path")
    if isinstance(path, list) and path and all(isinstance(item, int) for item in path):
        shown = [labels[item] for item in path if item in labels]
        if shown:
            value["path_files"] = _collapse(shown)
    for key, child in list(value.items()):
        if key in _FILE_KEYS and isinstance(child, str):
            value[key] = relative_repo_path(child, root)
        elif key == "files" and isinstance(child, list):
            value[key] = [relative_repo_path(item, root) if isinstance(item, str) else item for item in child]
        else:
            annotate_flow_paths(child, labels, root)


def _collapse(items: list[str]) -> list[str]:
    collapsed: list[str] = []
    for item in items:
        if not collapsed or collapsed[-1] != item:
            collapsed.append(item)
    return collapsed
