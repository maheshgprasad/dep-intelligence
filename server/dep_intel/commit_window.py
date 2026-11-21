"""List commits in a timeframe with PyDriller.

The result is only a commit list and the existing base/head pair that covers
that net change. Ranking, Boundary Gate, and validation are unchanged.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def window_bounds(since: str, until: str) -> tuple[datetime, datetime]:
    start = _parse_day(since, end=False)
    end = _parse_day(until, end=True)
    if start is None or end is None:
        raise ValueError("Choose the start and end dates for the timeframe.")
    if start > end:
        raise ValueError("The timeframe ends before it starts.")
    return start, end


def commits_in_window(repo: Path, since: str, until: str) -> dict[str, Any]:
    try:
        start, end = window_bounds(since, until)
    except ValueError as exc:
        return _empty(str(exc))
    try:
        from pydriller import Repository
    except ImportError:
        return _empty("PyDriller is not installed.")
    found: list[dict[str, str]] = []
    try:
        for commit in Repository(str(repo), since=start, to=end, order="date").traverse_commits():
            parents = list(commit.parents or [])
            stamp = commit.committer_date
            found.append({
                "sha": str(commit.hash or ""),
                "parent": str(parents[0]) if parents else "",
                "committed_at": stamp.isoformat() if stamp is not None else "",
                "subject": str(commit.msg or "").splitlines()[0][:160],
            })
    except Exception as exc:
        text = str(exc).strip().splitlines()
        return _empty(text[0][:240] if text else "Commit history could not be read.")
    found = [item for item in found if item["sha"]]
    found.sort(key=lambda item: (item["committed_at"], item["sha"]))
    oldest = found[0] if found else {}
    newest = found[-1] if found else {}
    visible = found[:40]
    return {
        "ok": True,
        "error": "",
        "commits": [{"sha": item["sha"], "committed_at": item["committed_at"], "subject": item["subject"]} for item in visible],
        "commit_count": len(found),
        "base_ref": str(oldest.get("parent") or ""),
        "head_ref": str(newest.get("sha") or ""),
    }


def as_review_source(source: dict[str, Any], repo: Path) -> dict[str, Any]:
    """Rewrite a timeframe into the commit or range source the review already accepts."""
    if str(source.get("kind") or "") != "timeframe":
        return source
    window = commits_in_window(repo, str(source.get("since") or ""), str(source.get("until") or ""))
    if not window["ok"]:
        raise ValueError(window["error"] or "Commit history could not be read.")
    if not window["head_ref"]:
        raise ValueError("No commits in this window.")
    rewritten = dict(source)
    if window["base_ref"]:
        rewritten["kind"] = "range"
        rewritten["base_ref"] = window["base_ref"]
        rewritten["head_ref"] = window["head_ref"]
        rewritten["commit"] = None
    else:
        rewritten["kind"] = "commit"
        rewritten["commit"] = window["head_ref"]
        rewritten["base_ref"] = None
        rewritten["head_ref"] = None
    return rewritten


def _parse_day(value: str, *, end: bool) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    if len(text) == 10:
        text = f"{text}T23:59:59" if end else f"{text}T00:00:00"
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("Use dates in yyyy-mm-dd form.") from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _empty(error: str) -> dict[str, Any]:
    return {"ok": False, "error": error, "commits": [], "commit_count": 0, "base_ref": "", "head_ref": ""}
