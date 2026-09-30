"""Project paths and .env loading.

Clients (Cursor, Claude, IBM Bob) spawn the MCP server with whatever working
directory they choose. Paths are resolved from this file, then overridden by
environment variables or a project .env file.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SERVER_ROOT = PROJECT_ROOT / "server"


def _placeholder(value: str) -> bool:
    return (
        not value
        or "/absolute/path/" in value
        or "your_ibm_ghe_token" in value
        or "your_public_github_token" in value
        or value.startswith("ghp_your")
    )


def load_dotenv(path: Path | None = None) -> None:
    env_file = path or (PROJECT_ROOT / ".env")
    if not env_file.is_file():
        return
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if _placeholder(value) or os.environ.get(key):
            continue
        os.environ[key] = value


@dataclass(frozen=True)
class Settings:
    root: Path
    repos_file: Path
    output_dir: Path
    github_token: str
    ghe_token: str

    @classmethod
    def load(cls, repos_file: str = "", output_dir: str = "") -> "Settings":
        load_dotenv()
        repos = Path(repos_file or os.environ.get("REPOS_FILE") or (PROJECT_ROOT / "repos.txt"))
        output = Path(output_dir or os.environ.get("OUTPUT_DIR") or (PROJECT_ROOT / "output"))
        if not repos.is_absolute():
            repos = PROJECT_ROOT / repos
        if not output.is_absolute():
            output = PROJECT_ROOT / output
        output.mkdir(parents=True, exist_ok=True)
        return cls(
            root=PROJECT_ROOT,
            repos_file=repos,
            output_dir=output,
            github_token=os.environ.get("GITHUB_TOKEN", ""),
            ghe_token=os.environ.get("GHE_TOKEN", ""),
        )
