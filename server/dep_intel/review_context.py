"""Bounded context for a review. Context is not policy."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from dep_intel.paths import contained
from dep_intel.review_models import DEFAULT_BUDGETS


def collect_context(repo: Path | None, change: dict[str, Any], impact: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    budgets = {**DEFAULT_BUDGETS, **(config.get("budgets") or {})}
    per_file = int(budgets["max_context_bytes_per_file"])
    total_limit = int(budgets["max_total_context_bytes"])
    file_limit = int(budgets["max_context_files"])
    items: list[dict[str, Any]] = []
    used = 0
    truncated = False
    reasons: list[str] = []

    def add(kind: str, identity: str, text: str) -> None:
        nonlocal used, truncated
        if len(items) >= file_limit:
            truncated = True
            if "max_context_files" not in reasons:
                reasons.append("max_context_files")
            return
        snippet = text[:per_file]
        if used + len(snippet) > total_limit:
            snippet = snippet[: max(0, total_limit - used)]
            truncated = True
            if "max_total_context_bytes" not in reasons:
                reasons.append("max_total_context_bytes")
        if not snippet:
            return
        used += len(snippet.encode("utf-8"))
        items.append({"kind": kind, "id": identity, "text": snippet, "truncated": len(snippet) < len(text)})

    context_cfg = config.get("context") or {}
    if repo is not None:
        for name in context_cfg.get("instruction_files") or []:
            path = repo / name
            if path.is_file() and contained(path.resolve(), repo.resolve()):
                add("instruction", name, _read(path))
        for item in change.get("_files") or []:
            path = item.get("new_path") or item.get("old_path")
            if not path:
                continue
            snippet = _hunk_window(repo, path, item.get("hunks") or [])
            if snippet:
                add("changed_code", path, snippet)
    for candidate in (impact.get("ranked_candidates") or [])[: int(budgets["max_nrm_candidates"])]:
        origin = candidate.get("evidence_origin") or ""
        statement = candidate.get("statement") or ""
        add("nrm", f"{candidate.get('service_id')}:{candidate.get('file_path')}", f"{origin} {statement}".strip())
    return {
        "items": items,
        "bytes": used,
        "truncated": truncated,
        "truncation_reasons": reasons,
        "note": "Context explains the repository. It does not by itself create a gate decision.",
    }


def _hunk_window(repo: Path, relative: str, hunks: list[dict[str, Any]]) -> str:
    if ".." in Path(relative).parts or relative.startswith("/"):
        return ""
    path = (repo / relative).resolve()
    if not contained(path, repo.resolve()) or not path.is_file():
        return ""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    if not hunks:
        return "\n".join(lines[:40])
    chunks = []
    for hunk in hunks[:8]:
        start = max(1, int(hunk.get("new_start") or 1) - 3)
        end = start + int(hunk.get("new_count") or 1) + 6
        numbered = [f"{index}|{lines[index - 1]}" for index in range(start, min(end, len(lines)) + 1)]
        chunks.append("\n".join(numbered))
    return "\n\n".join(chunks)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""
