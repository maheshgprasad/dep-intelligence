"""File churn and co-change from git history.

Local checkouts use git log. GitHub URLs use the commits API, capped so a
scan stays bounded. Structural relationships come from the code-review-graph
MCP results, not from this report.
"""

from __future__ import annotations

import subprocess
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import httpx

from dep_intel.config import Settings
from dep_intel.sources import Workspace
from dep_intel.store import now, write_json


def analyze_commits(
    workspaces: list[Workspace],
    settings: Settings,
    client: httpx.Client,
    since_days: int = 365,
) -> dict:
    index = []
    for workspace in workspaces:
        if workspace.ref.kind == "local" and workspace.ref.path:
            report = _from_git(workspace, since_days)
        elif workspace.ref.kind == "github":
            report = _from_github(workspace, settings, client, since_days)
        else:
            report = _empty(workspace, "No checkout to mine.")
        slug_dir = settings.output_dir / "graphs" / workspace.ref.slug
        slug_dir.mkdir(parents=True, exist_ok=True)
        write_json(slug_dir, "cochange.json", report)
        index.append(
            {
                "slug": workspace.ref.slug,
                "name": workspace.ref.name,
                "commit_count": report["commit_count"],
                "window_label": report["window_label"],
            }
        )
    payload = {"meta": {"generated_at": now(), "source": "dep-intel"}, "repos": index}
    write_json(settings.output_dir, "cochange_index.json", payload)
    commits = sum(item["commit_count"] for item in index)
    return {"success": True, "message": f"Commit history: {commits} commits across {len(index)} repositories."}


def _from_git(workspace: Workspace, since_days: int) -> dict:
    root = workspace.ref.path
    since = ""
    if since_days > 0:
        since = (datetime.now(timezone.utc) - timedelta(days=since_days)).date().isoformat()
    command = ["git", "-C", str(root), "log", "--numstat", "--pretty=format:commit %H|%aI|%an"]
    if since:
        command.append(f"--since={since}")
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return _empty(workspace, "git log failed.")
    if completed.returncode != 0:
        return _empty(workspace, "Not a git repository.")
    return _summarize(workspace, completed.stdout, since_days)


def _from_github(workspace: Workspace, settings: Settings, client: httpx.Client, since_days: int) -> dict:
    ref = workspace.ref
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "dep-intel"}
    token = settings.github_token if ref.host == "github.com" else (settings.ghe_token or settings.github_token)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    base = "https://api.github.com" if ref.host == "github.com" else f"https://{ref.host}/api/v3"
    since = ""
    params: dict[str, str | int] = {"per_page": 30}
    if since_days > 0:
        since = (datetime.now(timezone.utc) - timedelta(days=since_days)).date().isoformat()
        params["since"] = f"{since}T00:00:00Z"
    try:
        listing = client.get(f"{base}/repos/{ref.owner}/{ref.repo}/commits", params=params, headers=headers, timeout=20)
    except httpx.HTTPError:
        return _empty(workspace, "GitHub commit list failed.")
    if listing.status_code != 200:
        return _empty(workspace, f"GitHub commit list returned {listing.status_code}.")
    chunks = []
    for item in (listing.json() or [])[:30]:
        sha = item.get("sha")
        if not sha:
            continue
        detail = client.get(f"{base}/repos/{ref.owner}/{ref.repo}/commits/{sha}", headers=headers, timeout=20)
        if detail.status_code != 200:
            continue
        body = detail.json()
        commit = body.get("commit") or {}
        author = (commit.get("author") or {}).get("name") or "unknown"
        date = (commit.get("author") or {}).get("date") or ""
        chunks.append(f"commit {sha}|{date}|{author}")
        for file in body.get("files") or []:
            filename = file.get("filename") or ""
            chunks.append(f"{file.get('additions') or 0}\t{file.get('deletions') or 0}\t{filename}")
    return _summarize(workspace, "\n".join(chunks), since_days)


def _summarize(workspace: Workspace, text: str, since_days: int) -> dict:
    churn: dict[str, dict] = {}
    pair_counts: dict[tuple[str, str], int] = defaultdict(int)
    authors: set[str] = set()
    commit_count = 0
    current_files: list[str] = []

    def flush() -> None:
        unique = sorted(set(current_files))
        for index, left in enumerate(unique):
            for right in unique[index + 1 :]:
                pair_counts[(left, right)] += 1

    for line in text.splitlines():
        if line.startswith("commit "):
            flush()
            current_files = []
            commit_count += 1
            parts = line.split("|")
            if len(parts) >= 3:
                authors.add(parts[2])
            continue
        columns = line.split("\t")
        if len(columns) < 3 or columns[0] == "-":
            continue
        added, deleted, filename = int(columns[0] or 0), int(columns[1] or 0), columns[2]
        current_files.append(filename)
        slot = churn.setdefault(
            filename,
            {"file": filename, "commits": 0, "additions": 0, "deletions": 0, "authors": set()},
        )
        slot["commits"] += 1
        slot["additions"] += added
        slot["deletions"] += deleted
    flush()
    rows = []
    for slot in churn.values():
        rows.append(
            {
                "file": slot["file"],
                "commits": slot["commits"],
                "additions": slot["additions"],
                "deletions": slot["deletions"],
                "authors": len(slot["authors"]) or 1,
                "churn_score": slot["commits"] * (slot["additions"] + slot["deletions"] + 1),
            }
        )
    rows.sort(key=lambda item: item["churn_score"], reverse=True)
    commits_by_file = {item["file"]: item["commits"] for item in rows}
    cochange = []
    for (left, right), count in pair_counts.items():
        left_commits = commits_by_file.get(left) or count
        right_commits = commits_by_file.get(right) or count
        coupling = round(count / min(left_commits, right_commits), 2)
        cochange.append(
            {
                "file_a": left,
                "file_b": right,
                "commits": count,
                "coupling": coupling,
            }
        )
    cochange.sort(key=lambda item: item["coupling"], reverse=True)
    label = "all time" if since_days <= 0 else f"{since_days} days"
    return {
        "slug": workspace.ref.slug,
        "repo_url": workspace.ref.raw,
        "analysed_at": now(),
        "window_label": label,
        "commit_count": commit_count,
        "author_count": len(authors),
        "hotspots": rows[:15],
        "churn": rows,
        "cochange": cochange[:40],
    }


def _empty(workspace: Workspace, message: str) -> dict:
    return {
        "slug": workspace.ref.slug,
        "repo_url": workspace.ref.raw,
        "analysed_at": now(),
        "window_label": "",
        "commit_count": 0,
        "author_count": 0,
        "hotspots": [],
        "churn": [],
        "cochange": [],
        "message": message,
    }
