"""Bounded Git metadata for the review form. Commands are argument arrays."""

from __future__ import annotations

from pathlib import Path

from dep_intel.config import Settings
from dep_intel.review_sources import repository_allowed, run_git


def source_options(repo: Path, settings: Settings) -> dict:
    if not repo.exists() or not repository_allowed(repo, settings):
        return {"repository": str(repo), "error": "repository_not_allowed"}
    current = _one(repo, ["rev-parse", "--abbrev-ref", "HEAD"])
    default_raw = _one(repo, ["symbolic-ref", "--short", "refs/remotes/origin/HEAD"])
    default_branch = default_raw.split("/")[-1] if default_raw else "main"
    status = _one(repo, ["status", "--porcelain"], limit=100_000)
    lines = [line for line in status.splitlines() if line.strip()]
    staged = sum(1 for line in lines if len(line) > 1 and line[0] not in {" ", "?"})
    branches = [
        line.strip()
        for line in _one(
            repo,
            ["for-each-ref", "--sort=-committerdate", "--count=40", "--format=%(refname:short)", "refs/heads"],
            limit=8_000,
        ).splitlines()
        if line.strip()
    ][:40]
    commits = []
    raw = _one(repo, ["log", "-n", "30", "--format=%H%x1f%h%x1f%s%x1f%cI"], limit=20_000)
    for line in raw.splitlines():
        parts = line.split("\x1f")
        if len(parts) < 4 or not parts[0]:
            continue
        commits.append({
            "sha": parts[0],
            "short_sha": parts[1][:12],
            "subject": parts[2][:160],
            "committed_at": parts[3],
        })
    return {
        "repository": str(repo),
        "current_branch": current,
        "default_branch": default_branch or "main",
        "dirty_files": len(lines),
        "staged_files": staged,
        "branches": branches,
        "recent_commits": commits[:30],
    }


def _one(repo: Path, args: list[str], limit: int = 400) -> str:
    code, stdout, _stderr = run_git(repo, args, timeout=8, limit=limit)
    if code != 0:
        return ""
    return stdout.strip()
