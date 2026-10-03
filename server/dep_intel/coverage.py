"""Test coverage. Python runs pytest. Other languages report the spec status."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from dep_intel.config import Settings
from dep_intel.sources import Workspace
from dep_intel.store import now, read_json, write_json

REAL = {"python", "go"}
MOCK_ONLY = {"javascript", "typescript"}


def run_coverage(workspaces: list[Workspace], settings: Settings) -> dict:
    languages = read_json(settings.output_dir, "language_detection.json") or {}
    detected = languages.get("repositories") or {}
    if not detected:
        from dep_intel.languages import detect

        detect(workspaces, settings)
        languages = read_json(settings.output_dir, "language_detection.json") or {}
        detected = languages.get("repositories") or {}
    repositories = {}
    for workspace in workspaces:
        info = detected.get(workspace.ref.name) or {}
        key = info.get("language_key") or "unknown"
        repositories[workspace.ref.name] = _one(workspace, key)
    real = sum(1 for item in repositories.values() if item["status"] == "real")
    payload = {
        "meta": {"generated_at": now(), "source": "dep-intel", "real_results": real},
        "repositories": repositories,
    }
    write_json(settings.output_dir, "test_coverage.json", payload)
    return {"success": True, "message": f"Coverage finished. {real} repositories produced real results."}


def _one(workspace: Workspace, language: str) -> dict:
    base = {"language": language, "percent": None, "files": []}
    root = workspace.ref.path
    if root and root.is_dir() and language == "python":
        result = _pytest(root)
        return {**base, **_note_cleanup(result, workspace)}
    if root and root.is_dir() and language == "go":
        result = _go(root)
        return {**base, **_note_cleanup(result, workspace)}
    if workspace.note:
        return {**base, "status": "unavailable", "message": workspace.note}
    if language in MOCK_ONLY:
        return {
            **base,
            "status": "unavailable",
            "message": "Real JavaScript coverage is not wired up. Python and Go run locally when tests exist.",
        }
    if language not in REAL:
        return {
            **base,
            "status": "unavailable",
            "message": f"Test coverage is not implemented for {language}.",
        }
    return {
        **base,
        "status": "unavailable",
        "message": "Real coverage runs against a local checkout. Clone the repository or list a local path in repos.txt.",
    }


def _note_cleanup(result: dict, workspace: Workspace) -> dict:
    if workspace.content_source != "shallow_clone":
        return result
    message = result.get("message") or ""
    suffix = " The shallow checkout is deleted after this report is saved."
    if suffix.strip() not in message:
        result = {**result, "message": f"{message}{suffix}"}
    return result


def _pytest(root: Path) -> dict:
    report = root / ".dep-intel-coverage.json"
    try:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "--cov",
                "--cov-report",
                f"json:{report}",
                "-q",
                "--noconftest",
            ],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"status": "unavailable", "message": f"pytest did not run: {exc}"}
    if not report.is_file():
        detail = (completed.stdout or completed.stderr or "pytest did not write coverage").strip().splitlines()
        tail = detail[-1] if detail else "pytest did not write coverage"
        return {"status": "unavailable", "message": f"pytest did not produce a coverage report. {tail}"}
    data = json.loads(report.read_text(encoding="utf-8"))
    report.unlink(missing_ok=True)
    files = []
    for path, info in (data.get("files") or {}).items():
        summary = info.get("summary") or {}
        files.append(
            {
                "path": path,
                "percent": summary.get("percent_covered"),
                "missing_lines": info.get("missing_lines") or [],
            }
        )
    totals = (data.get("totals") or {}).get("percent_covered")
    return {
        "status": "real",
        "message": "Real coverage data from pytest",
        "percent": totals,
        "files": files,
    }


def _go(root: Path) -> dict:
    try:
        completed = subprocess.run(
            ["go", "test", "-coverprofile=coverage.out", "-covermode=count", "./..."],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"status": "unavailable", "message": f"go test did not run: {exc}"}
    profile = root / "coverage.out"
    if completed.returncode != 0 or not profile.is_file():
        return {"status": "unavailable", "message": "go test did not produce a coverage profile"}
    text = profile.read_text(encoding="utf-8")
    profile.unlink(missing_ok=True)
    return {"status": "real", "message": "Real coverage data from go test", "percent": _go_percent(text), "files": []}


def _go_percent(profile: str) -> float | None:
    covered = 0
    total = 0
    for line in profile.splitlines():
        if line.startswith("mode:") or not line.strip():
            continue
        parts = line.split()
        if len(parts) < 3:
            continue
        statements = int(parts[1])
        hits = int(parts[2])
        total += statements
        if hits:
            covered += statements
    if not total:
        return None
    return round(covered / total * 100, 1)
