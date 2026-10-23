"""Run configured verification commands. The model never supplies the command."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Any

from dep_intel.paths import contained
from dep_intel.review_models import diagnostic
from dep_intel.sources import redact

_SECRET_ENV = {"GITHUB_TOKEN", "GHE_TOKEN", "NAMI_REVIEW_API_KEY", "GH_TOKEN", "NAMI_GIT_TOKEN"}


def run_verifications(
    config: dict[str, Any],
    change: dict[str, Any],
    policy_results: list[dict[str, Any]],
    repo: Path,
    *,
    enabled: bool,
    cancel: Any | None = None,
    secrets: list[str] | None = None,
) -> list[dict[str, Any]]:
    verification = config.get("verification") or {}
    commands = {item.get("id"): item for item in verification.get("commands") or [] if isinstance(item, dict) and item.get("id")}
    requested = [item for item in policy_results if item.get("check") == "required_command" and item.get("result") == "requested"]
    selected = list(commands.values()) if not requested else [commands.get(item.get("command_id")) for item in requested]
    results = []
    if not enabled or not verification.get("enabled", True):
        for command in selected:
            if command is None:
                continue
            results.append(_row(command, "skipped", 0, None, "", "Verification is disabled for this review."))
        return results
    timeout = int(verification.get("timeout_seconds") or 300)
    allow_network = bool(verification.get("allow_network"))
    for command in selected:
        if command is None:
            results.append({
                "id": "unknown",
                "command": [],
                "status": "skipped",
                "duration_ms": 0,
                "exit_code": None,
                "output": "",
                "message": "The policy names a command that is not configured.",
                "diagnostic": diagnostic("invalid_policy", "Verification command is not allowlisted.", severity="error"),
            })
            continue
        if command.get("requires_network") and not allow_network:
            results.append(_row(command, "skipped", 0, None, "", "This command may access the network and allow_network is false. NBG does not sandbox the process."))
            continue
        if not _paths_match(command, change):
            results.append(_row(command, "skipped", 0, None, "", "No changed path matched the command scope."))
            continue
        results.append(_execute(command, repo, timeout=timeout, cancel=cancel, secrets=secrets or []))
    return results


def apply_verification_findings(findings: list[dict[str, Any]], results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    updated = []
    for finding in findings:
        command_id = (finding.get("structured") or {}).get("command_id")
        if not command_id:
            updated.append(finding)
            continue
        match = next((item for item in results if item.get("id") == command_id), None)
        if match is None:
            updated.append(finding)
            continue
        status = match.get("status")
        cloned = {**finding, "verification_status": status}
        if status == "failed":
            cloned["evidence_strength"] = "verified"
            cloned["category"] = "verification_failure"
            cloned["claim"] = f"Required verification {command_id} failed with exit code {match.get('exit_code')}."
            cloned["blocking"] = True
        elif status in {"timeout", "skipped"}:
            cloned["verification_status"] = status
            cloned["evidence_strength"] = "moderate"
        updated.append(cloned)
    for item in results:
        if item.get("status") != "failed":
            continue
        if any((finding.get("structured") or {}).get("command_id") == item.get("id") for finding in updated):
            continue
        updated.append({
            "finding_id": f"verify-{item.get('id')}",
            "category": "verification_failure",
            "severity": "high",
            "title": f"Verification {item.get('id')} failed",
            "claim": item.get("message") or "A configured verification command failed.",
            "file_path": None,
            "line_start": None,
            "source": "verification_command",
            "policy_id": None,
            "evidence_strength": "verified",
            "blocking": True,
            "verification_status": "failed",
            "evidence": [{
                "evidence_id": f"verify-evidence-{item.get('id')}",
                "kind": "verification",
                "source": "verification_command",
                "summary": (item.get("output") or "")[:500],
                "structured_data": {"exit_code": item.get("exit_code"), "command": item.get("command")},
                "stale": False,
            }],
            "structured": {"command_id": item.get("id")},
        })
    return updated


def _execute(command: dict[str, Any], repo: Path, *, timeout: int, cancel: Any | None, secrets: list[str]) -> dict[str, Any]:
    args = command.get("command") or []
    if not isinstance(args, list) or not args or not all(isinstance(item, str) for item in args):
        return _row(command, "skipped", 0, None, "", "The command must be an argument array.")
    cwd = (repo / str(command.get("cwd") or ".")).resolve()
    if not contained(cwd, repo.resolve()):
        return _row(command, "skipped", 0, None, "", "The working directory escapes the repository.")
    if cancel is not None and getattr(cancel, "is_set", lambda: False)():
        return _row(command, "cancelled", 0, None, "", "Verification was cancelled.")
    env = {key: value for key, value in os.environ.items() if key not in _SECRET_ENV}
    started = time.perf_counter()
    try:
        process = subprocess.Popen(
            args,
            cwd=str(cwd),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            shell=False,
        )
    except OSError as exc:
        return _row(command, "failed", 0, None, "", redact(str(exc), secrets))
    try:
        stdout, stderr = process.communicate(timeout=timeout)
        status = "passed" if process.returncode == 0 else "failed"
        code = process.returncode
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate()
        status = "timeout"
        code = None
    elapsed = int((time.perf_counter() - started) * 1000)
    output = redact(f"{stdout or ''}{stderr or ''}", secrets)
    truncated = False
    if len(output) > 8192:
        output = output[:8192]
        truncated = True
    row = _row(command, status, elapsed, code, output, "passed" if status == "passed" else status)
    row["truncated"] = truncated
    row["network_policy"] = "allow_network is recorded only. NBG does not install a network sandbox."
    return row


def _paths_match(command: dict[str, Any], change: dict[str, Any]) -> bool:
    patterns = command.get("paths") or ["**"]
    from dep_intel.review_policy import path_match

    paths = [item.get("new_path") or item.get("old_path") or "" for item in change.get("changed_files") or []]
    if not paths:
        return False
    return any(any(path_match(pattern, path) for pattern in patterns) for path in paths if path)


def _row(command: dict[str, Any], status: str, duration: int, exit_code: int | None, output: str, message: str) -> dict[str, Any]:
    return {
        "id": command.get("id"),
        "command": list(command.get("command") or []),
        "cwd": command.get("cwd") or ".",
        "status": status,
        "duration_ms": duration,
        "exit_code": exit_code,
        "output": output,
        "message": message,
        "required": bool(command.get("required")),
    }
