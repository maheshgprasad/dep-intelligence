"""Resolve a review request to commits and a machine-readable diff.

Git is invoked with argument arrays. Refs that fail to resolve stay unresolved.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from dep_intel.config import Settings
from dep_intel.paths import contained
from dep_intel.review_models import DEFAULT_BUDGETS, diagnostic
from dep_intel.sources import load_repos, redact

_PR = re.compile(r"^https?://(?P<host>[^/]+)/(?P<owner>[^/]+)/(?P<repo>[^/]+)/pull/(?P<number>\d+)/?$")
_UNSAFE_REF = re.compile(r"[\x00\n\r;|&`$]")


def repository_allowed(path: Path, settings: Settings) -> bool:
    """Allow an allowlisted repository or a checkout Nami owns under output/."""
    try:
        resolved = path.resolve()
    except OSError:
        return False
    if contained(resolved, settings.output_dir.resolve()):
        return True
    for repo in load_repos(settings):
        if repo.path is not None and contained(resolved, repo.path.resolve()):
            return True
        candidate = (settings.root / repo.raw).resolve() if repo.raw else None
        if candidate is not None and (resolved == candidate or contained(resolved, candidate)):
            return True
    return False


def safe_ref(ref: str) -> bool:
    if not ref or ref.startswith("-") or _UNSAFE_REF.search(ref):
        return False
    if ".." in ref.split("/"):
        return False
    return True


def run_git(
    repo: Path,
    args: list[str],
    *,
    timeout: int = 30,
    limit: int = DEFAULT_BUDGETS["max_git_output_bytes"],
    secrets: list[str] | None = None,
) -> tuple[int, str, str]:
    """Run git inside ``repo``. Never uses a shell."""
    command = ["git", "-C", str(repo), *args]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            shell=False,
        )
    except subprocess.TimeoutExpired:
        return 124, "", "git timed out"
    except OSError as exc:
        return 127, "", redact(str(exc), secrets)
    stdout = completed.stdout or ""
    stderr = redact(completed.stderr or "", secrets)
    truncated = False
    if len(stdout) > limit:
        stdout = stdout[:limit]
        truncated = True
    if truncated:
        stderr = (stderr + "\n[diff output truncated]").strip()
    return completed.returncode, stdout, stderr


def resolve_change(repo: Path, source: dict[str, Any], settings: Settings, budgets: dict[str, int] | None = None) -> dict[str, Any]:
    """Return SHAs, diff text, and diagnostics. Unresolved refs are not replaced."""
    limits = {**DEFAULT_BUDGETS, **(budgets or {})}
    kind = source.get("kind") or "working_tree"
    diagnostics: list[dict[str, Any]] = []
    if not repository_allowed(repo, settings):
        return _empty(kind, diagnostics=[diagnostic("review_repository_not_allowlisted", f"{repo} is not an allowlisted repository or a tool-owned checkout.", severity="error")])
    if kind == "explicit":
        return {
            "kind": kind,
            "repository": str(repo),
            "base_sha": source.get("base_ref") or None,
            "head_sha": source.get("head_ref") or None,
            "committed_at": None,
            "title": source.get("title") or "",
            "description": "",
            "diff_text": "",
            "name_status": "",
            "numstat": "",
            "explicit_files": list(source.get("files") or []),
            "diagnostics": diagnostics,
            "pr": {},
        }
    if kind == "github_pr":
        return _pull_request(repo, source, settings, limits, diagnostics)
    if not (repo / ".git").exists() and not _git_ok(repo):
        diagnostics.append(diagnostic("git_ref_not_found", f"{repo} is not a Git repository.", severity="error"))
        return _empty(kind, diagnostics=diagnostics, repository=str(repo))
    if kind == "working_tree":
        return _worktree(repo, "working_tree", ["diff", "-M", "--unified=0", "--no-color", "--no-ext-diff"], limits, diagnostics, staged=False)
    if kind == "staged":
        return _worktree(repo, "staged", ["diff", "--cached", "-M", "--unified=0", "--no-color", "--no-ext-diff"], limits, diagnostics, staged=True)
    if kind == "commit":
        commit = str(source.get("commit") or "")
        if not safe_ref(commit):
            diagnostics.append(diagnostic("git_ref_not_found", "The commit ref is empty or unsafe.", severity="error"))
            return _empty(kind, diagnostics=diagnostics, repository=str(repo))
        sha, error = _rev_parse(repo, commit, limits)
        if error:
            diagnostics.append(diagnostic("git_ref_not_found", error, severity="error"))
            return _empty(kind, diagnostics=diagnostics, repository=str(repo))
        parent, parent_error = _rev_parse(repo, f"{sha}^", limits)
        base = parent if not parent_error else None
        if parent_error:
            diagnostics.append(diagnostic("git_ref_not_found", "The commit has no parent. The diff is against the empty tree.", severity="info"))
            code, diff_text, err = run_git(repo, ["diff", "-M", "--unified=0", "--no-color", "--no-ext-diff", "--root", sha], timeout=limits["git_timeout_seconds"], limit=limits["max_diff_bytes"])
            name_code, name_status, _ = run_git(repo, ["diff-tree", "--root", "-M", "--name-status", "-z", "-r", sha], timeout=limits["git_timeout_seconds"])
            num_code, numstat, _ = run_git(repo, ["diff-tree", "--root", "-M", "--numstat", "-z", "-r", sha], timeout=limits["git_timeout_seconds"])
        else:
            code, diff_text, err = run_git(repo, ["diff", "-M", "--unified=0", "--no-color", "--no-ext-diff", base or "", sha], timeout=limits["git_timeout_seconds"], limit=limits["max_diff_bytes"])
            _, name_status, _ = run_git(repo, ["diff", "-M", "--name-status", "-z", base or "", sha], timeout=limits["git_timeout_seconds"])
            _, numstat, _ = run_git(repo, ["diff", "-M", "--numstat", "-z", base or "", sha], timeout=limits["git_timeout_seconds"])
        if code not in {0, None} and code != 0:
            diagnostics.append(diagnostic("git_ref_not_found", err or "git diff failed", severity="error"))
        return _resolved(kind, repo, base, sha, diff_text, name_status, numstat, diagnostics, limits)
    if kind == "range":
        base_ref = str(source.get("base_ref") or "")
        head_ref = str(source.get("head_ref") or "")
        if not safe_ref(base_ref) or not safe_ref(head_ref):
            diagnostics.append(diagnostic("git_ref_not_found", "Base and head refs must be present and cannot be shell options.", severity="error"))
            return _empty(kind, diagnostics=diagnostics, repository=str(repo))
        base, base_error = _rev_parse(repo, base_ref, limits)
        head, head_error = _rev_parse(repo, head_ref, limits)
        if base_error or head_error:
            diagnostics.append(diagnostic("git_ref_not_found", base_error or head_error, severity="error"))
            return _empty(kind, diagnostics=diagnostics, repository=str(repo), base_sha=base, head_sha=head)
        code, diff_text, err = run_git(repo, ["diff", "-M", "--unified=0", "--no-color", "--no-ext-diff", base, head], timeout=limits["git_timeout_seconds"], limit=limits["max_diff_bytes"])
        _, name_status, _ = run_git(repo, ["diff", "-M", "--name-status", "-z", base, head], timeout=limits["git_timeout_seconds"])
        _, numstat, _ = run_git(repo, ["diff", "-M", "--numstat", "-z", base, head], timeout=limits["git_timeout_seconds"])
        if code != 0:
            diagnostics.append(diagnostic("git_ref_not_found", err or "git diff failed", severity="error"))
        return _resolved(kind, repo, base, head, diff_text, name_status, numstat, diagnostics, limits)
    diagnostics.append(diagnostic("invalid_policy", f"Unknown change source {kind}.", severity="error"))
    return _empty(kind, diagnostics=diagnostics, repository=str(repo))


def materialize_worktree(repo: Path, sha: str, dest: Path, *, timeout: int = 60) -> tuple[bool, str]:
    """Create a detached worktree. Does not check out or reset the user's tree."""
    if not safe_ref(sha):
        return False, "Refused to materialize an unsafe revision."
    dest.parent.mkdir(parents=True, exist_ok=True)
    code, _stdout, stderr = run_git(repo, ["worktree", "add", "--detach", str(dest), sha], timeout=timeout)
    if code != 0:
        return False, stderr or "git worktree add failed"
    return True, ""


def remove_worktree(repo: Path, dest: Path) -> None:
    run_git(repo, ["worktree", "remove", "--force", str(dest)], timeout=30)
    if dest.exists():
        return


def _pull_request(repo: Path, source: dict[str, Any], settings: Settings, limits: dict[str, int], diagnostics: list[dict]) -> dict[str, Any]:
    url = str(source.get("pr_url") or "")
    match = _PR.match(url.strip())
    if match is None:
        diagnostics.append(diagnostic("git_ref_not_found", "The pull request URL is not a GitHub pull request.", severity="error"))
        return _empty("github_pr", diagnostics=diagnostics, repository=str(repo))
    host = match.group("host")
    if "@" in host or host.startswith("-"):
        diagnostics.append(diagnostic("git_ref_not_found", "The pull request host is not accepted.", severity="error"))
        return _empty("github_pr", diagnostics=diagnostics, repository=str(repo))
    owner, name, number = match.group("owner"), match.group("repo"), match.group("number")
    api = _api_root(host)
    token = settings.github_token if host == "github.com" else settings.ghe_token
    if host != "github.com" and not token:
        diagnostics.append(diagnostic("github_auth_failed", f"No token is configured for {host}.", severity="error"))
        return _empty("github_pr", diagnostics=diagnostics, repository=str(repo))
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "nami-boundary-gate"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        with httpx.Client(timeout=20.0, follow_redirects=True) as client:
            response = client.get(f"{api}/repos/{owner}/{name}/pulls/{number}", headers=headers)
            if response.status_code in {401, 403}:
                diagnostics.append(diagnostic("github_auth_failed", "GitHub refused the pull request request.", severity="error"))
                return _empty("github_pr", diagnostics=diagnostics, repository=str(repo))
            if response.status_code != 200:
                diagnostics.append(diagnostic("git_ref_not_found", f"GitHub returned {response.status_code} for the pull request.", severity="error"))
                return _empty("github_pr", diagnostics=diagnostics, repository=str(repo))
            payload = response.json()
    except httpx.HTTPError as exc:
        diagnostics.append(diagnostic("git_ref_not_found", redact(str(exc), [token]), severity="error"))
        return _empty("github_pr", diagnostics=diagnostics, repository=str(repo))
    base = ((payload.get("base") or {}).get("sha")) or ""
    head = ((payload.get("head") or {}).get("sha")) or ""
    if not base or not head:
        diagnostics.append(diagnostic("git_ref_not_found", "The pull request response did not include base and head SHAs.", severity="error"))
        return _empty("github_pr", diagnostics=diagnostics, repository=str(repo))
    pr = {
        "url": url,
        "number": int(number),
        "title": payload.get("title") or "",
        "description": payload.get("body") or "",
        "base_sha": base,
        "head_sha": head,
    }
    if _git_ok(repo):
        local = resolve_change(repo, {"kind": "range", "base_ref": base, "head_ref": head}, settings, limits)
        local["kind"] = "github_pr"
        local["pr"] = pr
        local["title"] = pr["title"]
        local["diagnostics"] = diagnostics + list(local.get("diagnostics") or [])
        return local
    diagnostics.append(diagnostic("git_ref_not_found", "The pull request SHAs were resolved, and no local checkout was available to diff them.", severity="warning"))
    body = _empty("github_pr", diagnostics=diagnostics, repository=str(repo), base_sha=base, head_sha=head)
    body["pr"] = pr
    body["title"] = pr["title"]
    body["description"] = pr["description"]
    return body


def _api_root(host: str) -> str:
    if host == "github.com":
        return "https://api.github.com"
    return f"https://{host}/api/v3"


def _worktree(repo: Path, kind: str, diff_args: list[str], limits: dict[str, int], diagnostics: list[dict], *, staged: bool) -> dict[str, Any]:
    head, error = _rev_parse(repo, "HEAD", limits)
    if error:
        diagnostics.append(diagnostic("git_ref_not_found", error, severity="error"))
        return _empty(kind, diagnostics=diagnostics, repository=str(repo))
    code, diff_text, err = run_git(repo, diff_args, timeout=limits["git_timeout_seconds"], limit=limits["max_diff_bytes"])
    status_args = ["diff", "--cached" if staged else "", "-M", "--name-status", "-z"]
    num_args = ["diff", "--cached" if staged else "", "-M", "--numstat", "-z"]
    status_args = [item for item in status_args if item]
    num_args = [item for item in num_args if item]
    _, name_status, _ = run_git(repo, status_args, timeout=limits["git_timeout_seconds"])
    _, numstat, _ = run_git(repo, num_args, timeout=limits["git_timeout_seconds"])
    if code != 0:
        diagnostics.append(diagnostic("git_ref_not_found", err or "git diff failed", severity="error"))
    if diff_text or name_status:
        diagnostics.append(diagnostic("uncommitted_worktree", "The review diff is uncommitted. Base and head both resolve to HEAD.", severity="info"))
    return _resolved(kind, repo, head, head, diff_text, name_status, numstat, diagnostics, limits)


def _resolved(kind: str, repo: Path, base: str | None, head: str | None, diff_text: str, name_status: str, numstat: str, diagnostics: list[dict], limits: dict[str, int]) -> dict[str, Any]:
    committed_at = _committed_at(repo, head, limits) if head else None
    if len(diff_text) >= limits["max_diff_bytes"]:
        diagnostics.append(diagnostic("change_too_large", "The diff reached the configured byte budget and was truncated.", severity="warning"))
    return {
        "kind": kind,
        "repository": str(repo),
        "base_sha": base,
        "head_sha": head,
        "committed_at": committed_at,
        "title": "",
        "description": "",
        "diff_text": diff_text,
        "name_status": name_status,
        "numstat": numstat,
        "explicit_files": [],
        "diagnostics": diagnostics,
        "pr": {},
        "truncated_diff": len(diff_text) >= limits["max_diff_bytes"],
    }


def _committed_at(repo: Path, sha: str | None, limits: dict[str, int]) -> str | None:
    if not sha:
        return None
    code, stdout, _stderr = run_git(repo, ["log", "-1", "--format=%cI", sha], timeout=limits["git_timeout_seconds"])
    stamp = stdout.strip()
    if code != 0 or not stamp:
        return None
    return stamp


def _rev_parse(repo: Path, ref: str, limits: dict[str, int]) -> tuple[str | None, str]:
    if not safe_ref(ref):
        return None, f"Ref {ref!r} was rejected."
    code, stdout, stderr = run_git(repo, ["rev-parse", "--verify", "--end-of-options", ref], timeout=limits["git_timeout_seconds"])
    sha = stdout.strip()
    if code != 0 or not sha:
        return None, stderr.strip() or f"Git could not resolve {ref}."
    return sha, ""


def _git_ok(repo: Path) -> bool:
    code, _stdout, _stderr = run_git(repo, ["rev-parse", "--is-inside-work-tree"], timeout=10)
    return code == 0


def _empty(kind: str, *, diagnostics: list[dict], repository: str = "", base_sha: str | None = None, head_sha: str | None = None) -> dict[str, Any]:
    return {
        "kind": kind,
        "repository": repository,
        "base_sha": base_sha,
        "head_sha": head_sha,
        "committed_at": None,
        "title": "",
        "description": "",
        "diff_text": "",
        "name_status": "",
        "numstat": "",
        "explicit_files": [],
        "diagnostics": diagnostics,
        "pr": {},
        "truncated_diff": False,
    }


def retain_worktrees() -> bool:
    return os.environ.get("NBG_RETAIN_WORKTREES", "").lower() in {"1", "true", "yes"}
