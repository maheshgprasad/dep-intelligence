"""Read-only extraction of a code-review-graph SQLite database.

Supported adapter: code-review-graph 2.3.8–2.3.9, metadata schema_version 13,
with nodes.kind / nodes.qualified_name and edges.kind / edges.source_qualified /
edges.target_qualified. Connections are short, read-only, and owned by the
calling thread. This module never writes CRG tables or journal mode.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable

from dep_intel.graph_models import (
    CODE_SYMBOL,
    FILE_ANCHOR,
    UNRESOLVED,
    Diagnostic,
    GraphEdge,
    GraphNode,
    Partition,
    symbol_key,
)
from dep_intel.paths import contained
from dep_intel.store import now

SUPPORTED_SCHEMA_VERSIONS = {13}
SUPPORTED_PACKAGE = "code-review-graph>=2.3.8,<2.4"
NODE_COLUMNS = (
    "id",
    "kind",
    "name",
    "qualified_name",
    "file_path",
    "line_start",
    "line_end",
    "language",
    "is_test",
    "file_hash",
    "extra",
)
EDGE_COLUMNS = (
    "id",
    "kind",
    "source_qualified",
    "target_qualified",
    "file_path",
    "line",
    "extra",
    "confidence",
    "confidence_tier",
    "target_resolution",
)
BATCH = 500


class UnsupportedSchema(RuntimeError):
    pass


class ExtractionFailed(RuntimeError):
    pass


def crg_package_version() -> str:
    try:
        import importlib.metadata as metadata

        return metadata.version("code-review-graph")
    except Exception:
        return ""


def database_identity(path: Path) -> str:
    try:
        stat = path.stat()
    except OSError:
        return ""
    return f"{stat.st_dev}:{stat.st_ino}:{stat.st_mtime_ns}:{stat.st_size}"


def extract_partition(
    db_path: Path,
    *,
    service_id: str,
    repository: str,
    repo_root: Path,
    package_version: str = "",
    workspace_head: str | None = None,
    after_begin: Callable[[], None] | None = None,
    retries: int = 3,
) -> Partition:
    """Read one repository partition. ``after_begin`` is a test hook."""
    observed = now()
    version = package_version or crg_package_version()
    if not contained(db_path, repo_root):
        raise ExtractionFailed(f"{db_path} is outside repository {repo_root}")
    if not db_path.is_file():
        raise ExtractionFailed(f"CRG database is absent: {db_path}")
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            return _read_once(
                db_path,
                service_id=service_id,
                repository=repository,
                repo_root=repo_root,
                package_version=version,
                workspace_head=workspace_head,
                observed=observed,
                after_begin=after_begin,
            )
        except sqlite3.OperationalError as exc:
            last_error = exc
            if "locked" not in str(exc).lower():
                raise ExtractionFailed(str(exc)) from exc
            time.sleep(0.05 * (2**attempt))
        except sqlite3.DatabaseError as exc:
            raise ExtractionFailed(f"CRG database is unreadable: {exc}") from exc
    raise ExtractionFailed(f"CRG database stayed locked: {last_error}")


def _read_once(
    db_path: Path,
    *,
    service_id: str,
    repository: str,
    repo_root: Path,
    package_version: str,
    workspace_head: str | None,
    observed: str,
    after_begin: Callable[[], None] | None,
) -> Partition:
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.0)
    cursor: sqlite3.Cursor | None = None
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        if after_begin is not None:
            after_begin()
        schema, metadata, stabilization = _inspect(connection)
        node_cols = _columns(connection, "nodes")
        edge_cols = _columns(connection, "edges")
        _require_adapter(schema, node_cols, edge_cols)
        cursor = connection.cursor()
        raw_nodes = _stream(cursor, "nodes", [name for name in NODE_COLUMNS if name in node_cols])
        raw_edges = _stream(cursor, "edges", [name for name in EDGE_COLUMNS if name in edge_cols])
        cursor.close()
        cursor = None
    finally:
        if cursor is not None:
            cursor.close()
        try:
            connection.rollback()
        except sqlite3.Error:
            pass
        connection.close()
    nodes, by_qualified, diagnostics = _nodes(raw_nodes, service_id, repo_root)
    edges, edge_diagnostics, unresolved = _edges(raw_edges, by_qualified, service_id, repo_root)
    nodes.update(unresolved)
    quality = "partial" if diagnostics or edge_diagnostics else "ok"
    return Partition(
        service_id=service_id,
        repository=repository,
        nodes=nodes,
        edges=edges,
        quality=quality,
        schema_version=schema,
        package_version=package_version,
        observed_at=observed,
        index_revision=metadata.get("last_updated"),
        workspace_head=workspace_head,
        stabilization=stabilization,
        db_identity=database_identity(db_path),
        diagnostics=diagnostics + edge_diagnostics,
    )


def _inspect(connection: sqlite3.Connection) -> tuple[str, dict[str, str], str]:
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "nodes" not in tables or "edges" not in tables:
        raise UnsupportedSchema("database has no nodes and edges tables")
    metadata: dict[str, str] = {}
    if "metadata" in tables:
        for key, value in connection.execute("SELECT key, value FROM metadata"):
            metadata[str(key)] = "" if value is None else str(value)
    schema = metadata.get("schema_version", "")
    stabilization = "metadata" if metadata.get("last_updated") else "none"
    return schema, metadata, stabilization


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}


def _require_adapter(schema: str, node_cols: set[str], edge_cols: set[str]) -> None:
    required_nodes = {"kind", "name", "qualified_name", "file_path"}
    required_edges = {"kind", "source_qualified", "target_qualified"}
    if not required_nodes <= node_cols or not required_edges <= edge_cols:
        raise UnsupportedSchema(
            "unsupported CRG schema: expected nodes.kind/qualified_name and "
            "edges.kind/source_qualified/target_qualified. "
            f"Found nodes={sorted(node_cols)} edges={sorted(edge_cols)}"
        )
    if schema and int(schema) not in SUPPORTED_SCHEMA_VERSIONS:
        raise UnsupportedSchema(
            f"unsupported CRG schema_version {schema}; this build supports {sorted(SUPPORTED_SCHEMA_VERSIONS)} "
            f"({SUPPORTED_PACKAGE})"
        )


def _stream(cursor: sqlite3.Cursor, table: str, columns: list[str]) -> list[dict[str, Any]]:
    selected = ", ".join(columns)
    cursor.execute(f"SELECT {selected} FROM {table}")
    rows: list[dict[str, Any]] = []
    while True:
        batch = cursor.fetchmany(BATCH)
        if not batch:
            break
        rows.extend(dict(zip(columns, item, strict=True)) for item in batch)
    return rows


def _nodes(
    rows: list[dict[str, Any]], service_id: str, repo_root: Path
) -> tuple[dict[str, GraphNode], dict[str, str], list[Diagnostic]]:
    nodes: dict[str, GraphNode] = {}
    by_qualified: dict[str, str] = {}
    diagnostics: list[Diagnostic] = []
    for row in rows:
        try:
            qualified_raw = str(row.get("qualified_name") or "")
            file_raw = str(row.get("file_path") or "")
            rel_file = relativize(file_raw, repo_root)
            lexical = relativize_qualified(qualified_raw, repo_root)
            kind = str(row.get("kind") or "")
            category = FILE_ANCHOR if kind == "File" else CODE_SYMBOL
            name = rel_file if kind == "File" and rel_file else str(row.get("name") or "")
            key = symbol_key(service_id, rel_file, lexical, kind or category)
            extra, extra_error = _json_object(row.get("extra"))
            if extra_error:
                diagnostics.append(
                    Diagnostic(
                        code="malformed_metadata",
                        message=f"Node {lexical} has malformed extra JSON",
                        service_id=service_id,
                        file=rel_file,
                    )
                )
            node = GraphNode(
                key=key,
                category=category,
                service_id=service_id,
                name=name,
                qualified_name=lexical,
                file_path=rel_file,
                line_start=_int_or_none(row.get("line_start")),
                line_end=_int_or_none(row.get("line_end")),
                language=str(row.get("language") or ""),
                kind=kind,
                crg_id=_int_or_none(row.get("id")),
                bounds_verified=True,
                is_test=bool(row.get("is_test")),
                metadata={
                    "file_hash": row.get("file_hash") or "",
                    "extra": extra,
                    "raw_qualified_name": qualified_raw,
                },
            )
            nodes[key] = node
            by_qualified[qualified_raw] = key
            by_qualified[lexical] = key
        except Exception as exc:
            diagnostics.append(
                Diagnostic(code="malformed_row", message=f"Skipped a node row: {exc}", service_id=service_id)
            )
    return nodes, by_qualified, diagnostics


def _edges(
    rows: list[dict[str, Any]],
    by_qualified: dict[str, str],
    service_id: str,
    repo_root: Path,
) -> tuple[list[GraphEdge], list[Diagnostic], dict[str, GraphNode]]:
    edges: list[GraphEdge] = []
    unresolved: dict[str, GraphNode] = {}
    diagnostics: list[Diagnostic] = []
    for row in rows:
        try:
            source_raw = str(row.get("source_qualified") or "")
            target_raw = str(row.get("target_qualified") or "")
            file_rel = relativize(str(row.get("file_path") or ""), repo_root)
            source_key = _lookup(by_qualified, source_raw, repo_root)
            target_key = _lookup(by_qualified, target_raw, repo_root)
            extra, extra_error = _json_object(row.get("extra"))
            if extra_error:
                diagnostics.append(
                    Diagnostic(
                        code="malformed_metadata",
                        message=f"Edge {source_raw} -> {target_raw} has malformed extra JSON",
                        service_id=service_id,
                        file=file_rel,
                        line=_int_or_none(row.get("line")),
                    )
                )
            if source_key is None:
                source_key = _unresolved(unresolved, by_qualified, service_id, source_raw, file_rel, row)
            if target_key is None:
                target_key = _unresolved(unresolved, by_qualified, service_id, target_raw, file_rel, row)
            edges.append(
                GraphEdge(
                    source=source_key,
                    target=target_key,
                    relation=str(row.get("kind") or ""),
                    evidence={
                        "source": "crg",
                        "status": "extracted",
                        "file": file_rel,
                        "line": _int_or_none(row.get("line")),
                        "confidence": row.get("confidence") if "confidence" in row else None,
                        "confidence_tier": row.get("confidence_tier") if "confidence_tier" in row else None,
                        "target_resolution": row.get("target_resolution"),
                        "crg_edge_id": row.get("id"),
                        "extra": extra,
                        "injected": False,
                        "extractor": "",
                    },
                )
            )
        except Exception as exc:
            diagnostics.append(
                Diagnostic(code="malformed_row", message=f"Skipped an edge row: {exc}", service_id=service_id)
            )
    return edges, diagnostics, unresolved


def _lookup(by_qualified: dict[str, str], raw: str, root: Path) -> str | None:
    return by_qualified.get(raw) or by_qualified.get(relativize_qualified(raw, root))


def _unresolved(
    bucket: dict[str, GraphNode],
    by_qualified: dict[str, str],
    service_id: str,
    raw: str,
    file_rel: str,
    row: dict[str, Any],
) -> str:
    existing = by_qualified.get(raw)
    if existing:
        return existing
    key = symbol_key(service_id, file_rel, raw, UNRESOLVED)
    by_qualified[raw] = key
    bucket[key] = GraphNode(
        key=key,
        category=UNRESOLVED,
        service_id=service_id,
        name=raw.rsplit("::", 1)[-1],
        qualified_name=raw,
        file_path=file_rel,
        line_start=_int_or_none(row.get("line")),
        kind=UNRESOLVED,
        bounds_verified=False,
        metadata={"resolved": False, "raw_qualified_name": raw},
    )
    return key


_ROOT_PREFIX: dict[str, str] = {}


def _root_prefix(root: Path) -> str:
    key = str(root)
    prefix = _ROOT_PREFIX.get(key)
    if prefix is None:
        prefix = root.resolve().as_posix().rstrip("/") + "/"
        _ROOT_PREFIX[key] = prefix
    return prefix


def relativize(value: str, root: Path) -> str:
    """Strip a repository root without resolving every row.

    CRG stores absolute paths. Resolving each one stats the filesystem and
    dominates extraction. A resolved root prefix is checked first. Paths that
    do not match, including links outside the checkout, fall back to one
    resolve so a sibling directory such as ``repo-evil`` is not treated as
    ``repo``.
    """
    if not value:
        return ""
    text = value.replace("\\", "/")
    prefix = _root_prefix(root)
    if text.startswith(prefix):
        return text[len(prefix) :]
    try:
        return Path(text).resolve().relative_to(prefix[:-1]).as_posix()
    except (OSError, ValueError):
        return text


def relativize_qualified(value: str, root: Path) -> str:
    if "::" in value:
        file_part, symbol = value.split("::", 1)
        relative = relativize(file_part, root)
        return f"{relative}::{symbol}" if relative else value
    return relativize(value, root)


def _json_object(value: Any) -> tuple[dict[str, Any], bool]:
    if value in (None, "", b""):
        return {}, False
    if isinstance(value, dict):
        return value, False
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}, True
    if isinstance(parsed, dict):
        return parsed, False
    return {}, False


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def stale_partition(previous: Partition, error: str) -> Partition:
    """Keep the last good nodes and edges, marked stale."""
    copy = Partition.from_dict(previous.to_dict())
    copy.quality = "stale"
    copy.error = error
    copy.diagnostics = list(copy.diagnostics) + [
        Diagnostic(code="extraction_failed", message=error, service_id=previous.service_id, severity="error")
    ]
    return copy
