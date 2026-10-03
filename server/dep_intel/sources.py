"""Read a repository from a local checkout or from GitHub."""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import httpx

from dep_intel.config import PROJECT_ROOT, Settings
from dep_intel.paths import contained

SKIP_DIRS = {
    ".git",
    ".venv",
    "node_modules",
    "dist",
    "build",
    "vendor",
    "__pycache__",
    "output",
}
TEXT_SUFFIXES = {
    ".js",
    ".jsx",
    ".mjs",
    ".ts",
    ".tsx",
    ".py",
    ".go",
    ".java",
    ".rb",
    ".php",
    ".rs",
    ".cs",
    ".cpp",
    ".cc",
    ".h",
    ".hpp",
    ".json",
    ".yml",
    ".yaml",
    ".toml",
    ".txt",
    ".mod",
    ".md",
}
MANIFEST_NAMES = {
    "package.json",
    "package-lock.json",
    "yarn.lock",
    "poetry.lock",
    "Pipfile.lock",
    "tsconfig.json",
    "go.mod",
    "go.sum",
    "requirements.txt",
    "setup.py",
    "pyproject.toml",
    "pom.xml",
    "build.gradle",
    "Gemfile",
    "composer.json",
    "Cargo.toml",
    "CMakeLists.txt",
    "Makefile",
}


@dataclass
class RepoRef:
    raw: str
    name: str
    slug: str
    kind: str
    path: Path | None = None
    host: str = ""
    owner: str = ""
    repo: str = ""


@dataclass
class Workspace:
    ref: RepoRef
    files: dict[str, str] = field(default_factory=dict)
    revision: str = ""
    content_source: str = ""
    truncated: bool = False
    dirty: bool = False
    stale: bool = False
    note: str = ""

    def read(self, relative: str) -> str:
        return self.files.get(relative, "")


@dataclass
class Checkout:
    path: Path
    revision: str = ""
    dirty: bool = False
    stale: bool = False
    owned: bool = False
    note: str = ""


def parse_repo_line(line: str, root: Path = PROJECT_ROOT) -> RepoRef:
    if line.startswith("http://") or line.startswith("https://"):
        parsed = urlparse(line)
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) < 2:
            raise ValueError(f"Not a repository URL: {line}")
        owner, repo = parts[0], parts[1].removesuffix(".git")
        name = f"{owner}/{repo}"
        return RepoRef(
            raw=line,
            name=name,
            slug=_slug(name),
            kind="github",
            host=parsed.netloc,
            owner=owner,
            repo=repo,
        )
    path = Path(line)
    if not path.is_absolute():
        path = root / path
    name = path.name
    return RepoRef(raw=line, name=name, slug=_slug(name), kind="local", path=path.resolve())


def load_repos(settings: Settings) -> list[RepoRef]:
    if not settings.repos_file.is_file():
        return []
    repos: list[RepoRef] = []
    for raw in settings.repos_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        repos.append(parse_repo_line(line, settings.root))
    return repos


def _slug(name: str) -> str:
    cleaned = []
    for char in name:
        cleaned.append(char if char.isalnum() else "_")
    return "".join(cleaned).strip("_") or "repo"


def slug_collisions(repos: list[RepoRef]) -> list[str]:
    seen: dict[str, str] = {}
    errors: list[str] = []
    for repo in repos:
        previous = seen.get(repo.slug)
        if previous and previous != repo.raw:
            errors.append(
                f"repository slug {repo.slug!r} collides between {previous!r} and {repo.raw!r}; "
                "refuse to overwrite one output with the other"
            )
        seen[repo.slug] = repo.raw
    return errors


def git_head(path: Path) -> str:
    if not path.exists():
        return ""
    completed = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()


def ensure_checkout(ref: RepoRef, settings: Settings, *, refresh: bool | None = None) -> Path:
    return ensure_checkout_info(ref, settings, refresh=refresh).path


def ensure_checkout_info(ref: RepoRef, settings: Settings, *, refresh: bool | None = None) -> Checkout:
    """Return a local checkout.

    User worktrees are never fetched or reset. Tool-owned clones under
    output/checkouts can be fetched when ``refresh`` is true, and only when
    they are clean. Credentials stay in an askpass helper, not in the URL.
    """
    if ref.kind == "local" and ref.path and ref.path.is_dir():
        return Checkout(path=ref.path, revision=git_head(ref.path), dirty=_dirty(ref.path), owned=False)
    dest = settings.output_dir / "checkouts" / ref.slug
    token = _token_for(ref, settings)
    do_refresh = settings.refresh_checkouts if refresh is None else refresh
    if (dest / ".git").exists():
        sanitize_tool_remote(dest, settings)
        dirty = _dirty(dest)
        if dirty:
            return Checkout(
                path=dest,
                revision=git_head(dest),
                dirty=True,
                stale=True,
                owned=True,
                note="dirty tool-owned checkout was left unchanged",
            )
        if do_refresh:
            _fetch_clean(dest, ref, token)
        return Checkout(path=dest, revision=git_head(dest), owned=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        raise RuntimeError(f"Checkout path exists without a git directory: {dest}")
    completed = _run_git(
        ["git", "clone", "--single-branch", "--no-tags", ref.raw, str(dest)],
        token=token,
        timeout=180,
    )
    if completed.returncode != 0 or not (dest / ".git").exists():
        detail = redact((completed.stderr or completed.stdout or "git clone failed").strip(), [token])
        raise RuntimeError(detail.splitlines()[-1][:300])
    sanitize_tool_remote(dest, settings)
    return Checkout(path=dest, revision=git_head(dest), owned=True)


@contextmanager
def analysis_sources(settings: Settings) -> Iterator[list[Workspace]]:
    """Shallow-clone GitHub allowlist entries for one analysis pass.

    Local paths are read in place and are never deleted. A GitHub URL is
    cloned with ``--depth 1`` under ``output/work``. Reports are written by
    the caller; this removes the temporary checkout when the pass finishes.
    """
    workspaces: list[Workspace] = []
    disposable: list[Path] = []
    originals: list[tuple[RepoRef, Path | None]] = []
    try:
        for ref in load_repos(settings):
            originals.append((ref, ref.path))
            if ref.kind == "local" and ref.path and ref.path.is_dir():
                checkout = Checkout(path=ref.path, revision=git_head(ref.path), dirty=_dirty(ref.path), owned=False)
                note = ""
            else:
                try:
                    checkout = _shallow_clone(ref, settings)
                    disposable.append(checkout.path)
                    note = ""
                except RuntimeError as exc:
                    checkout = None
                    note = str(exc)
            if checkout is None:
                workspaces.append(Workspace(ref=ref, content_source="shallow_clone", note=note))
                continue
            ref.path = checkout.path
            files, truncated = _read_local(checkout.path, 5000)
            workspaces.append(
                Workspace(
                    ref=ref,
                    files=files,
                    revision=checkout.revision or git_head(checkout.path),
                    content_source="local" if not checkout.owned else "shallow_clone",
                    truncated=truncated,
                    dirty=checkout.dirty,
                    stale=checkout.stale,
                    note=note,
                )
            )
        yield workspaces
    finally:
        for ref, path in originals:
            ref.path = path
        for path in disposable:
            shutil.rmtree(path, ignore_errors=True)


def _shallow_clone(ref: RepoRef, settings: Settings) -> Checkout:
    dest = settings.output_dir / "work" / ref.slug
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    token = _token_for(ref, settings)
    completed = _run_git(
        ["git", "clone", "--depth", "1", "--single-branch", "--no-tags", ref.raw, str(dest)],
        token=token,
        timeout=180,
    )
    if completed.returncode != 0 or not (dest / ".git").is_dir():
        shutil.rmtree(dest, ignore_errors=True)
        detail = redact((completed.stderr or completed.stdout or "shallow clone failed").strip(), [token])
        raise RuntimeError(detail.splitlines()[-1][:300] if detail else "shallow clone failed")
    return Checkout(path=dest, revision=git_head(dest), owned=True, note="shallow")


def load_workspace(ref: RepoRef, client: httpx.Client, settings: Settings, file_limit: int = 80) -> Workspace:
    if ref.kind == "local":
        files, truncated = _read_local(ref.path or Path("."), file_limit)
        return Workspace(
            ref=ref,
            files=files,
            revision=git_head(ref.path or Path(".")),
            content_source="local",
            truncated=truncated,
            dirty=_dirty(ref.path or Path(".")),
        )
    try:
        files, revision, truncated = _read_github(ref, client, settings, file_limit)
        return Workspace(ref=ref, files=files, revision=revision, content_source="github_api", truncated=truncated)
    except httpx.HTTPError:
        checkout = ensure_checkout_info(ref, settings)
        files, truncated = _read_local(checkout.path, file_limit)
        return Workspace(
            ref=ref,
            files=files,
            revision=checkout.revision,
            content_source="checkout",
            truncated=truncated,
            dirty=checkout.dirty,
            stale=checkout.stale,
        )


def read_contract_sources(root: Path, file_limit: int = 5000) -> tuple[dict[str, str], bool]:
    """Deterministic contract-source pass, separate from the 80-file workspace sample."""
    files, truncated = _read_local(root, file_limit, suffixes=_CONTRACT_SUFFIXES, manifests_unlimited=False)
    return files, truncated


_CONTRACT_SUFFIXES = {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".py", ".go", ".json", ".yml", ".yaml"}


def _read_local(
    root: Path,
    file_limit: int,
    suffixes: set[str] | None = None,
    manifests_unlimited: bool = True,
) -> tuple[dict[str, str], bool]:
    files: dict[str, str] = {}
    if not root.is_dir():
        return files, False
    allowed = suffixes or TEXT_SUFFIXES
    candidates: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames if name not in SKIP_DIRS and not name.startswith("."))
        for filename in sorted(filenames):
            path = Path(dirpath) / filename
            is_manifest = filename in MANIFEST_NAMES
            if not is_manifest and path.suffix.lower() not in allowed:
                continue
            candidates.append(path)
    candidates.sort(key=lambda path: path.relative_to(root).as_posix())
    extras = 0
    truncated = False
    for path in candidates:
        relative = path.relative_to(root).as_posix()
        filename = path.name
        is_manifest = manifests_unlimited and filename in MANIFEST_NAMES
        if not is_manifest and extras >= file_limit:
            truncated = True
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > 1_000_000 and filename not in {"package-lock.json", "yarn.lock", "poetry.lock", "go.sum"}:
            continue
        if size > 4_000_000:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        files[relative] = text
        if not is_manifest:
            extras += 1
    return files, truncated


def _token_for(ref: RepoRef, settings: Settings) -> str:
    if ref.host == "github.com":
        return settings.github_token
    return settings.ghe_token or settings.github_token


def _api_base(ref: RepoRef) -> str:
    if ref.host == "github.com":
        return "https://api.github.com"
    return f"https://{ref.host}/api/v3"


def _read_github(ref: RepoRef, client: httpx.Client, settings: Settings, file_limit: int) -> tuple[dict[str, str], str, bool]:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "dep-intel"}
    token = _token_for(ref, settings)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    base = _api_base(ref)
    meta = client.get(f"{base}/repos/{ref.owner}/{ref.repo}", headers=headers)
    meta.raise_for_status()
    branch = meta.json().get("default_branch") or "main"
    revision = ""
    branch_meta = client.get(f"{base}/repos/{ref.owner}/{ref.repo}/commits/{branch}", headers=headers)
    if branch_meta.status_code == 200:
        revision = str((branch_meta.json() or {}).get("sha") or "")
    pinned = revision or branch
    tree = client.get(
        f"{base}/repos/{ref.owner}/{ref.repo}/git/trees/{pinned}",
        params={"recursive": "1"},
        headers=headers,
    )
    tree.raise_for_status()
    paths = []
    for item in tree.json().get("tree", []):
        if item.get("type") != "blob":
            continue
        path = item.get("path") or ""
        name = path.rsplit("/", 1)[-1]
        suffix = Path(name).suffix.lower()
        if name in MANIFEST_NAMES or suffix in TEXT_SUFFIXES:
            paths.append(path)
    paths.sort(key=lambda path: (0 if Path(path).name in MANIFEST_NAMES else 1, path.count("/"), path))
    manifests = [path for path in paths if Path(path).name in MANIFEST_NAMES]
    others = [path for path in paths if Path(path).name not in MANIFEST_NAMES][:file_limit]
    files: dict[str, str] = {}
    for path in manifests + others:
        response = client.get(
            f"{base}/repos/{ref.owner}/{ref.repo}/contents/{path}",
            params={"ref": pinned},
            headers={**headers, "Accept": "application/vnd.github.raw"},
        )
        if response.status_code != 200:
            continue
        files[path] = response.text
    truncated = len(others) < len([path for path in paths if Path(path).name not in MANIFEST_NAMES])
    return files, revision, truncated


def redact(text: str, secrets: list[str] | None = None) -> str:
    cleaned = text or ""
    for secret in secrets or []:
        if secret:
            cleaned = cleaned.replace(secret, "[redacted]")
    cleaned = re.sub(r"x-access-token:[^@\s]+", "x-access-token:[redacted]", cleaned)
    cleaned = re.sub(r"ghp_[A-Za-z0-9]+", "[redacted]", cleaned)
    cleaned = re.sub(r"github_pat_[A-Za-z0-9_]+", "[redacted]", cleaned)
    return cleaned


def sanitize_tool_remote(dest: Path, settings: Settings) -> None:
    """Remove embedded credentials from a tool-owned origin URL only."""
    checkouts = (settings.output_dir / "checkouts").resolve()
    if not contained(dest, checkouts):
        return
    completed = subprocess.run(
        ["git", "-C", str(dest), "remote", "get-url", "origin"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if completed.returncode != 0:
        return
    current = completed.stdout.strip()
    cleaned = strip_userinfo(current)
    if cleaned == current:
        return
    subprocess.run(
        ["git", "-C", str(dest), "remote", "set-url", "origin", cleaned],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )


def strip_userinfo(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.username and not parsed.password:
        return url
    host = parsed.hostname or ""
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return urlunparse((parsed.scheme, host, parsed.path, parsed.params, parsed.query, parsed.fragment))


def _dirty(path: Path) -> bool:
    if not path.exists():
        return False
    completed = subprocess.run(
        ["git", "-C", str(path), "status", "--porcelain"],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if completed.returncode != 0:
        return False
    return bool(completed.stdout.strip())


def _fetch_clean(dest: Path, ref: RepoRef, token: str) -> None:
    fetched = _run_git(["git", "-C", str(dest), "fetch", "--prune", "origin"], token=token, timeout=180)
    if fetched.returncode != 0:
        return
    head = _run_git(["git", "-C", str(dest), "rev-parse", "--verify", "origin/HEAD"], timeout=15)
    if head.returncode != 0:
        head = _run_git(["git", "-C", str(dest), "rev-parse", "--verify", f"origin/{ref.repo}"], timeout=15)
    sha = head.stdout.strip()
    if head.returncode != 0 or not sha:
        return
    _run_git(["git", "-C", str(dest), "checkout", "--detach", sha], timeout=60)


def _run_git(args: list[str], token: str = "", timeout: int = 60) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    script_path = ""
    if token:
        handle = tempfile.NamedTemporaryFile("w", delete=False, prefix="nami-askpass-", suffix=".sh")
        handle.write(
            "#!/bin/sh\n"
            "case \"$1\" in\n"
            "*[Uu]sername*) printf '%s\\n' 'x-access-token' ;;\n"
            "*) printf '%s\\n' \"$NAMI_GIT_TOKEN\" ;;\n"
            "esac\n"
        )
        handle.close()
        script_path = handle.name
        os.chmod(script_path, stat.S_IRUSR | stat.S_IXUSR)
        env["GIT_ASKPASS"] = script_path
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["NAMI_GIT_TOKEN"] = token
    try:
        completed = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False, env=env)
    finally:
        if script_path:
            Path(script_path).unlink(missing_ok=True)
    completed.stdout = redact(completed.stdout, [token])
    completed.stderr = redact(completed.stderr, [token])
    return completed
