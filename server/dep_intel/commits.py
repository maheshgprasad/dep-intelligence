"""File churn, co-change, and directional temporal evidence from git history.

Local checkouts use git log. GitHub URLs use the commits API, capped so a
scan stays bounded. Structural relationships come from the code-review-graph
MCP results, not from this report. Independent repository histories are not
cross-repository co-change.
"""

from __future__ import annotations

import subprocess
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any

import httpx

from dep_intel.config import Settings
from dep_intel.evidence_bridge import normalize_relative_path
from dep_intel.sources import Workspace
from dep_intel.store import now, write_json

TEMPORAL_SCHEMA_VERSION = 1


@dataclass
class _Commit:
    sha: str
    committed_at: datetime | None
    author: str
    files: list[str] = field(default_factory=list)
    additions: dict[str, int] = field(default_factory=dict)
    deletions: dict[str, int] = field(default_factory=dict)


def analyze_commits(
    workspaces: list[Workspace],
    settings: Settings,
    client: httpx.Client,
    since_days: int = 365,
    until: str | None = None,
) -> dict:
    index = []
    cutoff = parse_cutoff(until)
    for workspace in workspaces:
        if workspace.ref.kind == "local" and workspace.ref.path:
            report, temporal = _from_git(workspace, since_days, cutoff)
        elif workspace.ref.kind == "github":
            report, temporal = _from_github(workspace, settings, client, since_days, cutoff)
        else:
            report, temporal = _empty(workspace, "No checkout to mine."), _empty_temporal(workspace, since_days, cutoff, "No checkout to mine.")
        slug_dir = settings.output_dir / "graphs" / workspace.ref.slug
        slug_dir.mkdir(parents=True, exist_ok=True)
        write_json(slug_dir, "cochange.json", report)
        write_json(slug_dir, "temporal_evidence.json", temporal)
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


def parse_cutoff(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = parse_iso(value)
    if parsed is None and len(value) == 10:
        parsed = parse_iso(f"{value}T23:59:59+00:00")
    return parsed


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc)


def _from_git(workspace: Workspace, since_days: int, until: datetime | None) -> tuple[dict, dict]:
    root = workspace.ref.path
    command = ["git", "-C", str(root), "log", "--numstat", "-M", "--pretty=format:commit %H|%aI|%an"]
    since = _since_date(since_days, until)
    if since:
        command.append(f"--since={since}")
    if until is not None:
        command.append(f"--until={until.isoformat()}")
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return _empty(workspace, "git log failed."), _empty_temporal(workspace, since_days, until, "git log failed.")
    if completed.returncode != 0:
        return _empty(workspace, "Not a git repository."), _empty_temporal(workspace, since_days, until, "Not a git repository.")
    return _history_reports(workspace, completed.stdout, since_days, until=until)


def _from_github(
    workspace: Workspace,
    settings: Settings,
    client: httpx.Client,
    since_days: int,
    until: datetime | None,
) -> tuple[dict, dict]:
    ref = workspace.ref
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "dep-intel"}
    token = settings.github_token if ref.host == "github.com" else (settings.ghe_token or settings.github_token)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    base = "https://api.github.com" if ref.host == "github.com" else f"https://{ref.host}/api/v3"
    since = _since_date(since_days, until)
    params: dict[str, str | int] = {"per_page": 30}
    if since:
        params["since"] = f"{since}T00:00:00Z"
    if until is not None:
        params["until"] = until.isoformat().replace("+00:00", "Z")
    try:
        listing = client.get(f"{base}/repos/{ref.owner}/{ref.repo}/commits", params=params, headers=headers, timeout=20)
    except httpx.HTTPError:
        return _empty(workspace, "GitHub commit list failed."), _empty_temporal(workspace, since_days, until, "GitHub commit list failed.")
    if listing.status_code != 200:
        message = f"GitHub commit list returned {listing.status_code}."
        return _empty(workspace, message), _empty_temporal(workspace, since_days, until, message)
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
        stamp = parse_iso(date)
        if until is not None and (stamp is None or stamp > until):
            continue
        chunks.append(f"commit {sha}|{date}|{author}")
        for file in body.get("files") or []:
            filename = file.get("filename") or ""
            chunks.append(f"{file.get('additions') or 0}\t{file.get('deletions') or 0}\t{filename}")
    return _history_reports(workspace, "\n".join(chunks), since_days, sample_limit=30, sampled=True, until=until)


def _summarize(workspace: Workspace, text: str, since_days: int, *, sample_limit: int | None = None, sampled: bool = False, until: datetime | None = None) -> dict:
    report, _temporal = _history_reports(workspace, text, since_days, sample_limit=sample_limit, sampled=sampled, until=until)
    return report


def _history_reports(
    workspace: Workspace,
    text: str,
    since_days: int,
    *,
    sample_limit: int | None = None,
    sampled: bool = False,
    until: datetime | None = None,
) -> tuple[dict, dict]:
    commits, binary_omitted, renames = _parse_commits(text, until)
    return (
        _cochange_report(workspace, commits, since_days, sample_limit, sampled, until, binary_omitted, renames),
        _temporal_report(workspace, commits, since_days, sample_limit, sampled, until),
    )


def _parse_commits(text: str, until: datetime | None) -> tuple[list[_Commit], int, list[dict[str, str]]]:
    commits: list[_Commit] = []
    current: _Commit | None = None
    binary_omitted = 0
    renames: list[dict[str, str]] = []
    skip = False

    def flush() -> None:
        nonlocal current
        if current is not None and not skip and current.files:
            commits.append(current)
        elif current is not None and not skip:
            commits.append(current)

    for line in text.splitlines():
        if line.startswith("commit "):
            flush()
            parts = line.split("|")
            sha = parts[0][7:].strip() if parts else ""
            stamp = parse_iso(parts[1] if len(parts) >= 2 else "")
            author = parts[2] if len(parts) >= 3 else ""
            skip = bool(until is not None and (stamp is None or stamp > until))
            current = _Commit(sha=sha, committed_at=stamp, author=author)
            continue
        if current is None or skip:
            continue
        columns = line.split("\t")
        if len(columns) < 3:
            continue
        if columns[0] == "-" or columns[1] == "-":
            binary_omitted += 1
            continue
        added, deleted, filename = int(columns[0] or 0), int(columns[1] or 0), columns[2]
        renamed_from = ""
        if " => " in filename:
            renamed_from, filename = _rename_names(filename)
            renames.append({"from": renamed_from, "to": filename})
        path = normalize_relative_path(filename)
        if not path:
            continue
        current.files.append(path)
        current.additions[path] = current.additions.get(path, 0) + added
        current.deletions[path] = current.deletions.get(path, 0) + deleted
    flush()
    epoch = datetime.min.replace(tzinfo=timezone.utc)
    commits.sort(key=lambda item: (item.committed_at is None, item.committed_at or epoch, item.sha))
    return commits, binary_omitted, renames


def _cochange_report(
    workspace: Workspace,
    commits: list[_Commit],
    since_days: int,
    sample_limit: int | None,
    sampled: bool,
    until: datetime | None,
    binary_omitted: int,
    renames: list[dict[str, str]],
) -> dict:
    churn: dict[str, dict] = {}
    pair_counts: dict[tuple[str, str], int] = defaultdict(int)
    authors: set[str] = set()
    for commit in commits:
        if commit.author:
            authors.add(commit.author)
        unique = sorted(set(commit.files))
        for index, left in enumerate(unique):
            for right in unique[index + 1 :]:
                pair_counts[(left, right)] += 1
        for filename in unique:
            slot = churn.setdefault(
                filename,
                {"file": filename, "commits": 0, "additions": 0, "deletions": 0, "authors": set()},
            )
            slot["commits"] += 1
            slot["additions"] += commit.additions.get(filename, 0)
            slot["deletions"] += commit.deletions.get(filename, 0)
            if commit.author:
                slot["authors"].add(commit.author)
    rows = []
    for slot in churn.values():
        rows.append(
            {
                "file": slot["file"],
                "commits": slot["commits"],
                "additions": slot["additions"],
                "deletions": slot["deletions"],
                "authors": len(slot["authors"]),
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
    since = _since_date(since_days)
    truncated = bool(sampled and sample_limit and len(commits) >= sample_limit)
    if sampled:
        label = f"sampled, at most {sample_limit} commits"
        if since:
            label += f" since {since}"
    else:
        label = "all time" if since_days <= 0 else f"{since_days} days"
    if until is not None:
        label += f" until {until.date().isoformat()}"
    return {
        "slug": workspace.ref.slug,
        "repo_url": workspace.ref.raw,
        "analysed_at": now(),
        "window_label": label,
        "history": {
            "since": since or None,
            "until": until.isoformat() if until else None,
            "sample_limit": sample_limit,
            "sampled": sampled,
            "truncated": truncated,
            "complete": not truncated,
            "binary_changes_omitted": binary_omitted,
            "renames": renames,
            "note": "Independent repository histories are not a shared commit event.",
        },
        "commit_count": len(commits),
        "author_count": len(authors),
        "hotspots": rows[:15],
        "churn": rows,
        "cochange": cochange[:40],
    }


def _temporal_report(
    workspace: Workspace,
    commits: list[_Commit],
    since_days: int,
    sample_limit: int | None,
    sampled: bool,
    until: datetime | None,
) -> dict:
    file_times: dict[str, list[str]] = defaultdict(list)
    file_counts: dict[str, int] = defaultdict(int)
    pair_support: dict[tuple[str, str], list[str]] = defaultdict(list)
    events = []
    for commit in commits:
        unique = sorted(set(commit.files))
        stamp = commit.committed_at.isoformat() if commit.committed_at else None
        events.append({"sha": commit.sha, "committed_at": stamp, "files": unique})
        for path in unique:
            file_counts[path] += 1
            if stamp:
                file_times[path].append(stamp)
        for source in unique:
            for target in unique:
                if source == target:
                    continue
                if stamp:
                    pair_support[(source, target)].append(stamp)
                else:
                    pair_support[(source, target)].append("")
    files = {}
    for path, count in sorted(file_counts.items()):
        times = [item for item in file_times.get(path, []) if item]
        files[path] = {
            "commit_count": count,
            "last_changed_at": times[-1] if times else None,
            "changed_at": times,
        }
    pairs = []
    for (source, target), stamps in pair_support.items():
        support = len(stamps)
        opportunities = file_counts.get(source) or 0
        rate = round(support / opportunities, 4) if opportunities else 0.0
        dated = [item for item in stamps if item]
        pairs.append(
            {
                "source_file": source,
                "target_file": target,
                "support": support,
                "source_opportunities": opportunities,
                "target_commits": file_counts.get(target) or 0,
                "directional_rate": rate,
                "last_supported_at": dated[-1] if dated else None,
                "median_lag_seconds": 0,
                "relation": "same_commit",
            }
        )
    pairs.sort(key=lambda item: (-item["directional_rate"], item["source_file"], item["target_file"]))
    truncated = bool(sampled and sample_limit and len(commits) >= sample_limit)
    return {
        "schema_version": TEMPORAL_SCHEMA_VERSION,
        "slug": workspace.ref.slug,
        "generated_at": now(),
        "history": _history_meta(since_days, until, complete=not truncated, sampled=sampled),
        "files": files,
        "pairs": pairs,
        "events": events,
        "note": "Directional same-repository history. Independent repositories are not a shared commit event.",
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
        "history": {"sampled": False, "truncated": False, "complete": False, "sample_limit": None, "until": None},
        "message": message,
    }


def _empty_temporal(workspace: Workspace, since_days: int, until: datetime | None, message: str) -> dict:
    return {
        "schema_version": TEMPORAL_SCHEMA_VERSION,
        "slug": workspace.ref.slug,
        "generated_at": now(),
        "history": _history_meta(since_days, until, complete=False, sampled=False),
        "files": {},
        "pairs": [],
        "events": [],
        "message": message,
        "note": "Directional same-repository history. Independent repositories are not a shared commit event.",
    }


def history_window_start(
    since_days: int,
    until: datetime | None = None,
    now_at: datetime | None = None,
) -> str:
    """Start of the mined window. A cutoff anchors the window; otherwise the clock does.

    ``until=2025-01-01`` and ``since_days=365`` covers 2024-01-02 through 2025-01-01,
    not "today minus 365 days" through that old cutoff.
    """
    if since_days <= 0:
        return ""
    anchor = until or now_at or datetime.now(timezone.utc)
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=timezone.utc)
    else:
        anchor = anchor.astimezone(timezone.utc)
    return (anchor - timedelta(days=since_days)).date().isoformat()


def history_anchor(until: datetime | None) -> str:
    return "cutoff" if until is not None else "current_time"


def _since_date(since_days: int, until: datetime | None = None, now_at: datetime | None = None) -> str:
    return history_window_start(since_days, until, now_at)


def _rename_names(filename: str) -> tuple[str, str]:
    # git numstat -M may wrap a rename as "old => new" or "{old => new}".
    if " => " not in filename:
        return "", filename
    left, right = filename.split(" => ", 1)
    left = left.replace("{", "")
    right = right.replace("}", "")
    if left.endswith("/") or "/" in left and not left.endswith(right.split("/", 1)[-1]):
        prefix = left.rsplit("/", 1)[0] + "/" if "/" in filename.split(" => ", 1)[0] else ""
        if "{" in filename:
            prefix = filename.split("{", 1)[0]
            return prefix + left, prefix + right
    return left, right


def recency_weight(last_supported_at: str | None, *, as_of: datetime | None, half_life_days: float = 90.0) -> float:
    stamp = parse_iso(last_supported_at)
    if stamp is None:
        return 0.0
    origin = as_of or datetime.now(timezone.utc)
    age_days = max(0.0, (origin - stamp).total_seconds() / 86400.0)
    if half_life_days <= 0:
        return 0.0
    return 2 ** (-age_days / half_life_days)


def temporal_rank_score(
    directional_rate: float,
    last_supported_at: str | None,
    support: int,
    *,
    as_of: datetime | None = None,
    half_life_days: float = 90.0,
) -> dict[str, Any]:
    recency = recency_weight(last_supported_at, as_of=as_of, half_life_days=half_life_days)
    support_factor = min(1.0, max(0.0, support / 3.0))
    score = max(0.0, min(1.0, directional_rate * recency * support_factor))
    return {
        "score": score,
        "directional_rate": directional_rate,
        "recency": recency,
        "support_factor": support_factor,
        "support": support,
        "score_kind": "temporal_evidence",
    }


def lagged_support(
    source_times: list[datetime],
    target_times: list[datetime],
    window: timedelta,
    *,
    matching: str = "one_to_one",
) -> dict[str, Any]:
    """Structure-conditioned lagged history. Observational, not causal, not co-change.

    ``one_to_one`` consumes each target event once. ``reusable_target`` may match
    the same target event to several source events (ablation only).
    """
    source_sorted = sorted(source_times)
    target_sorted = sorted(target_times)
    used: set[int] = set()
    supported = 0
    lags: list[float] = []
    last: datetime | None = None
    for source in source_sorted:
        match_index = next(
            (
                index
                for index, target in enumerate(target_sorted)
                if index not in used and source <= target <= source + window
            ),
            None,
        )
        if match_index is None:
            continue
        match = target_sorted[match_index]
        if matching != "reusable_target":
            used.add(match_index)
        supported += 1
        lags.append((match - source).total_seconds())
        if last is None or match > last:
            last = match
    opportunities = len(source_sorted)
    rate = (supported / opportunities) if opportunities else 0.0
    return {
        "source_events": opportunities,
        "supported_events": supported,
        "support": supported,
        "source_opportunities": opportunities,
        "support_rate": rate,
        "directional_rate": rate,
        "median_lag_seconds": median(lags) if lags else None,
        "last_supported_at": last.isoformat() if last else None,
        "evidence_kind": "structure_conditioned_lagged_history",
        "relation": "lagged_follow",
        "matching_strategy": "reusable_target" if matching == "reusable_target" else "one_to_one",
    }


def _history_meta(since_days: int, until: datetime | None, *, complete: bool, sampled: bool) -> dict[str, Any]:
    return {
        "since": history_window_start(since_days, until) or None,
        "until": until.isoformat() if until else None,
        "anchor": history_anchor(until),
        "complete": complete,
        "sampled": sampled,
        "include_merges": False,
    }
