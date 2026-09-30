"""Read a repository from a local checkout or from GitHub."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import httpx

from dep_intel.config import PROJECT_ROOT, Settings

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

    def read(self, relative: str) -> str:
        return self.files.get(relative, "")


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


def ensure_checkout(ref: RepoRef, settings: Settings) -> Path:
    """Return a local git checkout for code-review-graph and PyDriller.

    Local paths are used in place. GitHub URLs are cloned under output/checkouts.
    """
    if ref.kind == "local" and ref.path and ref.path.is_dir():
        return ref.path
    dest = settings.output_dir / "checkouts" / ref.slug
    if (dest / ".git").exists():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        raise RuntimeError(f"Checkout path exists without a git directory: {dest}")
    url = ref.raw
    token = _token_for(ref, settings)
    if token and url.startswith("https://"):
        url = url.replace("https://", f"https://x-access-token:{token}@", 1)
    import subprocess

    completed = subprocess.run(
        ["git", "clone", "--single-branch", "--no-tags", url, str(dest)],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if completed.returncode != 0 or not (dest / ".git").exists():
        detail = (completed.stderr or completed.stdout or "git clone failed").strip()
        raise RuntimeError(detail.splitlines()[-1][:300])
    return dest


def load_workspace(ref: RepoRef, client: httpx.Client, settings: Settings, file_limit: int = 80) -> Workspace:
    if ref.kind == "local":
        return Workspace(ref=ref, files=_read_local(ref.path or Path("."), file_limit))
    try:
        return Workspace(ref=ref, files=_read_github(ref, client, settings, file_limit))
    except httpx.HTTPError:
        checkout = ensure_checkout(ref, settings)
        return Workspace(ref=ref, files=_read_local(checkout, file_limit))


def _read_local(root: Path, file_limit: int) -> dict[str, str]:
    files: dict[str, str] = {}
    if not root.is_dir():
        return files
    extras = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in SKIP_DIRS and not name.startswith(".")]
        for filename in filenames:
            path = Path(dirpath) / filename
            relative = path.relative_to(root).as_posix()
            is_manifest = filename in MANIFEST_NAMES
            if not is_manifest and path.suffix.lower() not in TEXT_SUFFIXES:
                continue
            if not is_manifest and extras >= file_limit:
                continue
            if path.stat().st_size > 1_000_000 and filename not in {"package-lock.json", "yarn.lock", "poetry.lock", "go.sum"}:
                continue
            if path.stat().st_size > 4_000_000:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            files[relative] = text
            if not is_manifest:
                extras += 1
    return files


def _token_for(ref: RepoRef, settings: Settings) -> str:
    if ref.host == "github.com":
        return settings.github_token
    return settings.ghe_token or settings.github_token


def _api_base(ref: RepoRef) -> str:
    if ref.host == "github.com":
        return "https://api.github.com"
    return f"https://{ref.host}/api/v3"


def _read_github(ref: RepoRef, client: httpx.Client, settings: Settings, file_limit: int) -> dict[str, str]:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "dep-intel"}
    token = _token_for(ref, settings)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    base = _api_base(ref)
    meta = client.get(f"{base}/repos/{ref.owner}/{ref.repo}", headers=headers)
    meta.raise_for_status()
    branch = meta.json().get("default_branch") or "main"
    tree = client.get(
        f"{base}/repos/{ref.owner}/{ref.repo}/git/trees/{branch}",
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
            params={"ref": branch},
            headers={**headers, "Accept": "application/vnd.github.raw"},
        )
        if response.status_code != 200:
            continue
        files[path] = response.text
    return files
