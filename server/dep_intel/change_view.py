"""Normalize a diff into a Change View: files, hunks, symbols, and contracts."""

from __future__ import annotations

import difflib
import re
import uuid
from pathlib import Path, PurePosixPath
from typing import Any

from dep_intel.contract_extractors.javascript import extract_javascript
from dep_intel.contract_extractors.openapi import compare_operations, parse_openapi
from dep_intel.graph_models import CODE_SYMBOL, FILE_ANCHOR, HANDLER_ANCHOR, HTTP_CONTRACT, Snapshot
from dep_intel.review_models import DEFAULT_BUDGETS, diagnostic
from dep_intel.store import now

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_STATUS = {
    "A": "added",
    "M": "modified",
    "D": "deleted",
    "R": "renamed",
    "C": "renamed",
    "T": "mode_changed",
}
_SOURCE_EXT = {".py", ".js", ".jsx", ".mjs", ".ts", ".tsx", ".go", ".java", ".rs", ".rb", ".swift", ".kt"}
_BINARY_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".pdf", ".zip", ".wasm", ".so", ".dylib", ".exe", ".ico", ".woff", ".woff2",
    ".pt", ".pth", ".onnx", ".pkl", ".pickle", ".npy", ".npz", ".bin", ".safetensors", ".ckpt", ".h5", ".tflite", ".pb",
}
_LOCK = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "cargo.lock", "gemfile.lock"}
_MANIFEST = {"package.json", "requirements.txt", "pyproject.toml", "go.mod", "cargo.toml", "pom.xml", "gemfile"}


def safe_relative(path: str) -> str | None:
    if not path or path.startswith("/") or path.startswith("\\"):
        return None
    text = path.replace("\\", "/").strip()
    if text.startswith("a/") or text.startswith("b/"):
        # Only strip the git prefix when the caller already isolated one side.
        pass
    pure = PurePosixPath(text)
    if pure.is_absolute() or ".." in pure.parts:
        return None
    return pure.as_posix()


def classify_path(path: str) -> list[str]:
    """Deterministic file classes. An LLM is not asked what a manifest is."""
    name = PurePosixPath(path).name.lower()
    lower = path.replace("\\", "/").lower()
    suffix = PurePosixPath(lower).suffix
    classes: list[str] = []
    if name in _LOCK:
        classes.append("dependency_lockfile")
    elif name in _MANIFEST:
        classes.append("dependency_manifest")
    if "/.github/" in f"/{lower}" or lower.startswith(".github/") or ".gitlab-ci" in lower:
        classes.append("ci")
    if suffix == ".tf" or "/terraform/" in f"/{lower}":
        classes.append("iac")
    if "/migrations/" in f"/{lower}" or "/alembic/" in f"/{lower}":
        classes.append("database_migration")
    if suffix == ".proto":
        classes.append("protobuf")
    if "openapi" in name or "swagger" in name or suffix in {".yaml", ".yml"} and "openapi" in lower:
        classes.append("openapi")
    if suffix == ".avsc" or "asyncapi" in name:
        classes.append("event_schema")
    if "/generated/" in f"/{lower}" or lower.startswith("generated/") or "/dist/" in f"/{lower}":
        classes.append("generated")
    if suffix == ".md" or lower.startswith("docs/") or lower.startswith("doc/"):
        classes.append("documentation")
    if (
        "/test/" in f"/{lower}"
        or "/tests/" in f"/{lower}"
        or name.startswith("test_")
        or name.endswith("_test.py")
        or ".test." in name
        or ".spec." in name
    ):
        classes.append("test")
    if suffix in {".yml", ".yaml", ".toml", ".ini"} or name.endswith(".env.example"):
        classes.append("configuration")
    if name in {"dockerfile", "makefile"} or name.startswith("dockerfile"):
        classes.append("build")
    if suffix in _BINARY_EXT:
        classes.append("binary")
    if suffix in _SOURCE_EXT and "test" not in classes and "generated" not in classes:
        classes.append("source")
    if not classes:
        classes.append("unknown")
    return classes


def parse_name_status(blob: str) -> list[dict[str, Any]]:
    parts = blob.split("\0")
    rows: list[dict[str, Any]] = []
    index = 0
    while index < len(parts):
        status = parts[index]
        if not status:
            index += 1
            continue
        code = status[0]
        if code in {"R", "C"}:
            if index + 2 >= len(parts):
                break
            old, new = parts[index + 1], parts[index + 2]
            index += 3
            rows.append({"change_type": "renamed", "old_path": _clean_status_path(old), "new_path": _clean_status_path(new)})
            continue
        if index + 1 >= len(parts):
            break
        path = _clean_status_path(parts[index + 1])
        index += 2
        rows.append({"change_type": _STATUS.get(code, "modified"), "old_path": None if code == "A" else path, "new_path": None if code == "D" else path})
    return rows


def parse_numstat(blob: str) -> list[dict[str, Any]]:
    parts = blob.split("\0")
    rows: list[dict[str, Any]] = []
    index = 0
    while index < len(parts):
        chunk = parts[index]
        if not chunk:
            index += 1
            continue
        fields = chunk.split("\t")
        if len(fields) >= 3:
            adds, deletes, path = fields[0], fields[1], fields[-1]
            rows.append({"additions": _stat(adds), "deletions": _stat(deletes), "path": _clean_status_path(path), "binary": adds == "-" or deletes == "-"})
            index += 1
            continue
        if len(fields) == 2 and index + 2 < len(parts):
            adds, deletes = fields
            old, new = parts[index + 1], parts[index + 2]
            index += 3
            rows.append({
                "additions": _stat(adds),
                "deletions": _stat(deletes),
                "old_path": _clean_status_path(old),
                "new_path": _clean_status_path(new),
                "binary": adds == "-" or deletes == "-",
            })
            continue
        index += 1
    return rows


def parse_unified_diff(text: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Parse a unified diff. Malformed hunks become diagnostics instead of guesses."""
    diagnostics: list[dict[str, Any]] = []
    files: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    hunk: dict[str, Any] | None = None
    old_line = 0
    new_line = 0

    def flush_hunk() -> None:
        nonlocal hunk
        if current is not None and hunk is not None:
            current["hunks"].append(hunk)
        hunk = None

    def flush_file() -> None:
        nonlocal current
        flush_hunk()
        if current is not None:
            files.append(current)
        current = None

    for raw in text.splitlines():
        if raw.startswith("diff --git "):
            flush_file()
            old_path, new_path = _header_paths(raw)
            current = _blank_file(old_path, new_path)
            continue
        if current is None:
            if raw.startswith("@@"):
                diagnostics.append(diagnostic("malformed_diff", "A hunk appeared before a diff header.", severity="warning"))
            continue
        if raw.startswith("new file mode"):
            current["change_type"] = "added"
            continue
        if raw.startswith("deleted file mode"):
            current["change_type"] = "deleted"
            continue
        if raw.startswith("old mode") or raw.startswith("new mode"):
            if current["change_type"] == "modified" and not current["hunks"]:
                current["change_type"] = "mode_changed"
            continue
        if raw.startswith("rename from "):
            current["old_path"] = safe_relative(raw[len("rename from "):].strip())
            current["change_type"] = "renamed"
            continue
        if raw.startswith("rename to "):
            current["new_path"] = safe_relative(raw[len("rename to "):].strip())
            current["change_type"] = "renamed"
            continue
        if raw.startswith("Binary files ") or raw.startswith("GIT binary patch"):
            current["binary"] = True
            current["classifications"] = _with_binary(current)
            continue
        if raw.startswith("@@"):
            flush_hunk()
            match = _HUNK.match(raw)
            if match is None:
                diagnostics.append(diagnostic("malformed_diff", f"Could not parse hunk header: {raw[:80]}", severity="warning"))
                hunk = None
                continue
            old_start = int(match.group(1))
            old_count = int(match.group(2) or "1")
            new_start = int(match.group(3))
            new_count = int(match.group(4) or "1")
            hunk = {
                "old_start": old_start,
                "old_count": old_count,
                "new_start": new_start,
                "new_count": new_count,
                "changed_old_lines": [],
                "changed_new_lines": [],
                "added_lines": [],
                "removed_lines": [],
            }
            old_line = old_start
            new_line = new_start
            continue
        if hunk is None:
            continue
        if raw.startswith("\\"):
            continue
        if raw.startswith("+"):
            hunk["changed_new_lines"].append(new_line)
            hunk["added_lines"].append(raw[1:])
            new_line += 1
            continue
        if raw.startswith("-"):
            hunk["changed_old_lines"].append(old_line)
            hunk["removed_lines"].append(raw[1:])
            old_line += 1
            continue
        if raw.startswith(" "):
            old_line += 1
            new_line += 1
    flush_file()
    for item in files:
        if item["old_path"] is None and item["new_path"] is None:
            diagnostics.append(diagnostic("malformed_diff", "A diff header did not contain a safe relative path.", severity="warning"))
        path = item["new_path"] or item["old_path"] or ""
        item["classifications"] = classify_path(path) if path else ["unknown"]
        if item["binary"] and "binary" not in item["classifications"]:
            item["classifications"].append("binary")
    return files, diagnostics


def build_change_view(
    resolved: dict[str, Any],
    *,
    service_id: str,
    repository: str,
    snapshot: Snapshot | None,
    base_snapshot: Snapshot | None,
    repo_root: Path | None,
    budgets: dict[str, int] | None = None,
    graph_alignment: str = "unavailable",
) -> dict[str, Any]:
    limits = {**DEFAULT_BUDGETS, **(budgets or {})}
    diagnostics = list(resolved.get("diagnostics") or [])
    files, diff_diagnostics = _files_from(resolved)
    diagnostics.extend(diff_diagnostics)
    truncated = bool(resolved.get("truncated_diff"))
    reasons: list[str] = []
    if len(files) > limits["max_changed_files"]:
        files = files[: limits["max_changed_files"]]
        truncated = True
        reasons.append("max_changed_files")
    symbols: list[dict[str, Any]] = []
    contracts: list[dict[str, Any]] = []
    graph_version = snapshot.version if snapshot is not None else ""
    for item in files:
        item["service_id"] = service_id
        item["repository"] = repository
        item["classifications"] = item.get("classifications") or classify_path(item.get("new_path") or item.get("old_path") or "")
        mapped, symbol_diagnostics = map_changed_symbols(item, snapshot, base_snapshot, graph_version)
        symbols.extend(mapped)
        diagnostics.extend(symbol_diagnostics)
        contracts.extend(contract_changes(item, repo_root, resolved, snapshot, service_id))
    if len(symbols) > limits["max_structural_candidates"]:
        symbols = symbols[: limits["max_structural_candidates"]]
        truncated = True
        reasons.append("max_structural_candidates")
    return {
        "schema_version": 1,
        "change_id": uuid.uuid4().hex,
        "source": {"kind": resolved.get("kind"), "pr": resolved.get("pr") or {}},
        "repository": repository,
        "service_id": service_id,
        "base_sha": resolved.get("base_sha"),
        "head_sha": resolved.get("head_sha"),
        "generated_at": now(),
        "graph_alignment": graph_alignment,
        "graph_version": graph_version,
        "changed_files": [_public_file(item) for item in files],
        "changed_symbols": symbols,
        "contract_changes": contracts,
        "diagnostics": diagnostics,
        "budgets": limits,
        "truncated": truncated,
        "truncation_reasons": reasons,
        "_files": files,
    }


def map_changed_symbols(
    changed: dict[str, Any],
    snapshot: Snapshot | None,
    base_snapshot: Snapshot | None,
    graph_version: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Map hunks to the narrowest containing symbol. Deletions use the base graph."""
    diagnostics: list[dict[str, Any]] = []
    path = changed.get("new_path") or changed.get("old_path") or ""
    deleted = changed.get("change_type") == "deleted" or changed.get("new_path") is None
    source = base_snapshot if deleted and base_snapshot is not None else snapshot
    if source is None:
        if path:
            diagnostics.append(diagnostic("graph_missing", f"No graph is available to map {path}.", severity="info", file=path))
        return [], diagnostics
    lines = _changed_lines(changed, deleted=deleted)
    service = changed.get("service_id") or ""
    nodes = [
        node
        for node in source.nodes.values()
        if node.file_path == path and (not service or node.service_id == service)
    ]
    if not nodes:
        diagnostics.append(diagnostic("symbol_unresolved", f"No graph node matches {path}.", severity="info", file=path))
        return [], diagnostics
    if not lines and changed.get("change_type") == "deleted":
        lines = [node.line_start for node in nodes if node.line_start]
    chosen: dict[str, dict[str, Any]] = {}
    ambiguous = False
    for line in lines:
        containing = [
            node
            for node in nodes
            if node.line_start is not None
            and node.line_end is not None
            and node.line_start <= line <= node.line_end
            and node.category in {CODE_SYMBOL, HANDLER_ANCHOR, FILE_ANCHOR}
        ]
        symbols_only = [node for node in containing if node.category != FILE_ANCHOR]
        pool = symbols_only or containing
        if not pool:
            continue
        pool.sort(key=lambda node: ((node.line_end or 0) - (node.line_start or 0), node.key))
        narrow = (pool[0].line_end or 0) - (pool[0].line_start or 0)
        same = [node for node in pool if (node.line_end or 0) - (node.line_start or 0) == narrow]
        if len(same) > 1:
            ambiguous = True
        for node in same[:1]:
            slot = chosen.setdefault(
                node.key,
                {
                    "key": node.key,
                    "service_id": node.service_id,
                    "file_path": node.file_path,
                    "qualified_name": node.qualified_name,
                    "name": node.name,
                    "kind": node.kind or node.category,
                    "line_start": node.line_start,
                    "line_end": node.line_end,
                    "change_kinds": [],
                    "changed_lines": [],
                    "graph_version": graph_version,
                    "topology": "previous" if deleted and base_snapshot is not None else "current",
                },
            )
            kind = changed.get("change_type") or "modified"
            if kind not in slot["change_kinds"]:
                slot["change_kinds"].append(kind)
            if line not in slot["changed_lines"]:
                slot["changed_lines"].append(line)
    if not chosen:
        anchors = [node for node in nodes if node.category in {FILE_ANCHOR, HANDLER_ANCHOR} or node.kind == "File"]
        if len(anchors) == 1:
            node = anchors[0]
            chosen[node.key] = {
                "key": node.key,
                "service_id": node.service_id,
                "file_path": node.file_path,
                "qualified_name": node.qualified_name,
                "name": node.name,
                "kind": node.kind or node.category,
                "line_start": node.line_start,
                "line_end": node.line_end,
                "change_kinds": [changed.get("change_type") or "modified"],
                "graph_version": graph_version,
                "topology": "previous" if deleted and base_snapshot is not None else "current",
            }
        else:
            diagnostics.append(diagnostic("symbol_unresolved", f"Changed lines in {path} did not fall inside a symbol.", severity="info", file=path))
    if ambiguous:
        diagnostics.append(diagnostic("ambiguous_symbol", f"More than one symbol of the same span contains a changed line in {path}.", severity="info", file=path))
    return list(chosen.values()), diagnostics


def contract_changes(changed: dict[str, Any], repo_root: Path | None, resolved: dict[str, Any], snapshot: Snapshot | None, service_id: str) -> list[dict[str, Any]]:
    path = changed.get("new_path") or changed.get("old_path") or ""
    old_text, new_text = _file_texts(changed, repo_root, resolved)
    findings: list[dict[str, Any]] = []
    if path.endswith((".js", ".jsx", ".mjs")):
        old_routes = {(item.method.upper(), item.path): item for item in extract_javascript(path, old_text).routes} if old_text else {}
        new_routes = {(item.method.upper(), item.path): item for item in extract_javascript(path, new_text).routes} if new_text else {}
        for key, route in old_routes.items():
            if key not in new_routes:
                findings.append(_contract("operation_removed", route.method, route.path, path, route.line, breaking=True))
            else:
                current = new_routes[key]
                if current.handler_name != route.handler_name:
                    findings.append(_contract("handler_changed", route.method, route.path, path, current.line, breaking=False))
        for key, route in new_routes.items():
            if key not in old_routes:
                findings.append(_contract("operation_added", route.method, route.path, path, route.line, breaking=False))
    if "openapi" in (changed.get("classifications") or []) or "openapi" in path.lower() or "swagger" in path.lower():
        old_ops = parse_openapi(old_text, path).operations if old_text else []
        new_ops = parse_openapi(new_text, path).operations if new_text else []
        for item in compare_operations(old_ops, new_ops):
            kind = {
                "removed_operation": "operation_removed",
                "required_request_field": "required_request_field_added",
                "removed_response_field": "response_field_removed",
                "unknown": "unknown_schema_change",
            }.get(item["kind"], "unknown_schema_change")
            breaking = kind in {"operation_removed", "required_request_field_added", "response_field_removed"}
            findings.append(_contract(kind, item.get("method", ""), item.get("path", ""), path, None, breaking=breaking, message=item.get("message", "")))
        old_ids = {op.identity() for op in old_ops}
        for op in new_ops:
            if op.identity() not in old_ids:
                findings.append(_contract("operation_added", op.method, op.path, path, None, breaking=False))
    for item in findings:
        item["consumers"] = known_consumers(snapshot, service_id, item["method"], item["path"]) if item["breaking"] else []
        item["provenance"] = "extracted" if path.endswith((".js", ".jsx", ".mjs")) else "openapi"
    return findings


def known_consumers(snapshot: Snapshot | None, service_id: str, method: str, path: str) -> list[dict[str, str]]:
    if snapshot is None or not method or not path:
        return []
    suffix = f":{method.upper()}:{path}"
    contracts = [
        node
        for node in snapshot.nodes.values()
        if node.category == HTTP_CONTRACT and (not service_id or node.service_id == service_id) and suffix in str(node.metadata.get("identity") or node.qualified_name)
    ]
    keys = {node.key for node in contracts}
    consumers: list[dict[str, str]] = []
    seen: set[str] = set()
    for edge in snapshot.edges:
        if edge.relation != "CONSUMES_HTTP" or edge.target not in keys:
            continue
        caller = snapshot.nodes.get(edge.source)
        if caller is None or caller.key in seen:
            continue
        if service_id and caller.service_id == service_id:
            continue
        seen.add(caller.key)
        contract = snapshot.nodes.get(edge.target)
        consumers.append({
            "service_id": caller.service_id,
            "file_path": caller.file_path,
            "qualified_name": caller.qualified_name,
            "name": caller.name,
            "provenance": "cluster_graph",
            "contract_id": str(contract.metadata.get("identity") or "") if contract is not None else "",
        })
    return consumers


def graph_alignment(snapshot: Snapshot | None, service_id: str, base_sha: str | None, head_sha: str | None, *, dirty: bool) -> str:
    if snapshot is None or not service_id or service_id not in snapshot.partitions:
        return "unavailable"
    head = snapshot.partitions[service_id].workspace_head or ""
    if not head:
        return "unavailable"
    if head_sha and head == head_sha and not dirty:
        return "exact"
    if dirty and ((base_sha and head == base_sha) or (head_sha and head == head_sha)):
        return "compatible"
    return "stale"


def explicit_files_from(entries: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    files: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    for entry in entries:
        path = safe_relative(str(entry.get("path") or entry.get("new_path") or ""))
        if path is None:
            diagnostics.append(diagnostic("path_traversal", "An explicit path escaped the repository.", severity="error"))
            continue
        old = entry.get("old_text") or ""
        new = entry.get("new_text") or ""
        change_type = entry.get("change_type") or ("added" if old == "" and new else "deleted" if new == "" and old else "modified")
        hunks = entry.get("hunks") or _hunks_from_texts(old, new)
        files.append({
            **_blank_file(None if change_type == "added" else path, None if change_type == "deleted" else path),
            "change_type": change_type,
            "hunks": hunks,
            "old_text": old,
            "new_text": new,
            "classifications": classify_path(path),
        })
    return files, diagnostics


def _files_from(resolved: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if resolved.get("explicit_files"):
        return explicit_files_from(resolved["explicit_files"])
    parsed, diagnostics = parse_unified_diff(resolved.get("diff_text") or "")
    by_key = {(item.get("old_path"), item.get("new_path")): item for item in parsed}
    for row in parse_name_status(resolved.get("name_status") or ""):
        key = (row.get("old_path"), row.get("new_path"))
        match = by_key.get(key)
        if match is None:
            match = next((item for item in parsed if item.get("new_path") == row.get("new_path") or item.get("old_path") == row.get("old_path")), None)
        if match is None:
            created = _blank_file(row.get("old_path"), row.get("new_path"))
            created["change_type"] = row["change_type"]
            created["classifications"] = classify_path(row.get("new_path") or row.get("old_path") or "")
            parsed.append(created)
            continue
        match["change_type"] = row["change_type"]
        match["old_path"] = row.get("old_path")
        match["new_path"] = row.get("new_path")
    for row in parse_numstat(resolved.get("numstat") or ""):
        path = row.get("path") or row.get("new_path")
        match = next((item for item in parsed if item.get("new_path") == path or item.get("old_path") == path or item.get("new_path") == row.get("new_path")), None)
        if match is None:
            continue
        match["additions"] = row.get("additions")
        match["deletions"] = row.get("deletions")
        if row.get("binary"):
            match["binary"] = True
    kept = []
    for item in parsed:
        old_ok = item.get("old_path") is None or safe_relative(item["old_path"]) == item["old_path"]
        new_ok = item.get("new_path") is None or safe_relative(item["new_path"]) == item["new_path"]
        if not old_ok or not new_ok:
            diagnostics.append(diagnostic("path_traversal", "A diff path escaped the repository and was dropped.", severity="error"))
            continue
        kept.append(item)
    return kept, diagnostics


def _blank_file(old_path: str | None, new_path: str | None) -> dict[str, Any]:
    return {
        "old_path": safe_relative(old_path) if old_path else None,
        "new_path": safe_relative(new_path) if new_path else None,
        "change_type": "modified",
        "additions": None,
        "deletions": None,
        "binary": False,
        "old_sha": None,
        "new_sha": None,
        "hunks": [],
        "classifications": [],
    }


def _public_file(item: dict[str, Any]) -> dict[str, Any]:
    hunks = []
    for hunk in item.get("hunks") or []:
        hunks.append({
            "old_start": hunk["old_start"],
            "old_count": hunk["old_count"],
            "new_start": hunk["new_start"],
            "new_count": hunk["new_count"],
            "changed_old_lines": list(hunk.get("changed_old_lines") or []),
            "changed_new_lines": list(hunk.get("changed_new_lines") or []),
        })
    return {
        "service_id": item.get("service_id") or "",
        "repository": item.get("repository") or "",
        "old_path": item.get("old_path"),
        "new_path": item.get("new_path"),
        "change_type": item.get("change_type"),
        "additions": item.get("additions"),
        "deletions": item.get("deletions"),
        "binary": bool(item.get("binary")),
        "old_sha": item.get("old_sha"),
        "new_sha": item.get("new_sha"),
        "hunks": hunks,
        "classifications": list(item.get("classifications") or []),
    }


def _header_paths(header: str) -> tuple[str | None, str | None]:
    rest = header[len("diff --git "):].strip()
    tokens = _split_git_tokens(rest)
    if len(tokens) < 2:
        return None, None
    return _strip_side(tokens[0]), _strip_side(tokens[1])


def _split_git_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    current: list[str] = []
    quoted = False
    for char in text:
        if char == '"':
            quoted = not quoted
            current.append(char)
            continue
        if char == " " and not quoted:
            if current:
                tokens.append("".join(current))
                current = []
            continue
        current.append(char)
    if current:
        tokens.append("".join(current))
    return tokens


def _strip_side(token: str) -> str | None:
    if token.startswith('"') and token.endswith('"'):
        token = token[1:-1]
    if token.startswith("a/") or token.startswith("b/"):
        token = token[2:]
    if token == "/dev/null":
        return None
    return safe_relative(token)


def _clean_status_path(path: str) -> str | None:
    text = path.strip()
    if text.startswith('"') and text.endswith('"'):
        text = text[1:-1]
    return safe_relative(text)


def _stat(value: str) -> int | None:
    if value == "-":
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _with_binary(current: dict[str, Any]) -> list[str]:
    path = current.get("new_path") or current.get("old_path") or ""
    classes = classify_path(path) if path else ["binary"]
    if "binary" not in classes:
        classes.append("binary")
    return classes


def _changed_lines(changed: dict[str, Any], *, deleted: bool) -> list[int]:
    lines: list[int] = []
    for hunk in changed.get("hunks") or []:
        lines.extend(hunk.get("changed_old_lines") if deleted else hunk.get("changed_new_lines") or hunk.get("changed_old_lines") or [])
    return lines


def _file_texts(changed: dict[str, Any], repo_root: Path | None, resolved: dict[str, Any]) -> tuple[str, str]:
    if changed.get("binary") or "binary" in (changed.get("classifications") or []):
        return "", ""
    if "old_text" in changed or "new_text" in changed:
        return changed.get("old_text") or "", changed.get("new_text") or ""
    old = _show(repo_root, resolved.get("base_sha"), changed.get("old_path"))
    new = ""
    if changed.get("new_path") and repo_root is not None:
        candidate = repo_root / changed["new_path"]
        if candidate.is_file():
            from dep_intel.review_sources import read_utf8

            new = read_utf8(candidate)
        elif resolved.get("head_sha"):
            new = _show(repo_root, resolved.get("head_sha"), changed.get("new_path"))
    return old, new


def _show(repo: Path | None, sha: str | None, path: str | None) -> str:
    if repo is None or not sha or not path or safe_relative(path) != path:
        return ""
    from dep_intel.review_sources import run_git

    code, stdout, _stderr = run_git(repo, ["show", f"{sha}:{path}"], timeout=15)
    if code != 0 or "\ufffd" in stdout:
        return ""
    return stdout


def _hunks_from_texts(old: str, new: str) -> list[dict[str, Any]]:
    old_lines = old.splitlines()
    new_lines = new.splitlines()
    matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines)
    hunks = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        hunks.append({
            "old_start": i1 + 1,
            "old_count": i2 - i1,
            "new_start": j1 + 1,
            "new_count": j2 - j1,
            "changed_old_lines": list(range(i1 + 1, i2 + 1)),
            "changed_new_lines": list(range(j1 + 1, j2 + 1)),
            "added_lines": new_lines[j1:j2],
            "removed_lines": old_lines[i1:i2],
        })
    return hunks


def _contract(kind: str, method: str, path: str, file_path: str, line: int | None, *, breaking: bool, message: str = "") -> dict[str, Any]:
    return {
        "kind": kind,
        "method": method,
        "path": path,
        "file_path": file_path,
        "line": line,
        "breaking": breaking,
        "message": message or f"{kind} {method} {path}".strip(),
        "consumers": [],
        "provenance": "extracted",
    }
