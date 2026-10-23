"""Verification runs argument arrays inside the repository."""

from __future__ import annotations

import sys
import threading
from pathlib import Path

from dep_intel.review_verify import run_verifications


def test_success_failure_timeout_cancel_and_containment(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("print('ok')\n", encoding="utf-8")
    change = {"changed_files": [{"new_path": "app.py"}]}
    python = sys.executable
    passed = run_verifications(_config([python, "-c", "print('ok')"], required=False), change, [], tmp_path, enabled=True)
    assert passed[0]["status"] == "passed"
    assert passed[0]["exit_code"] == 0
    failed = run_verifications(_config([python, "-c", "raise SystemExit(3)"]), change, [], tmp_path, enabled=True)
    assert failed[0]["status"] == "failed"
    assert failed[0]["exit_code"] == 3
    timed = run_verifications(_config([python, "-c", "import time; time.sleep(5)"], timeout=1), change, [], tmp_path, enabled=True)
    assert timed[0]["status"] == "timeout"
    cancel = threading.Event()
    cancel.set()
    cancelled = run_verifications(_config([python, "-c", "print('no')"]), change, [], tmp_path, enabled=True, cancel=cancel)
    assert cancelled[0]["status"] == "cancelled"
    escaped = run_verifications(_config([python, "-c", "print('no')"], cwd="../"), change, [], tmp_path, enabled=True)
    assert escaped[0]["status"] == "skipped"
    noisy = run_verifications(_config([python, "-c", "print('x' * 20000)"]), change, [], tmp_path, enabled=True)
    assert len(noisy[0]["output"]) <= 8192
    secret = run_verifications(_config([python, "-c", "print('ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789')"]), change, [], tmp_path, enabled=True)
    assert "ghp_" not in secret[0]["output"]
    missing = run_verifications({"verification": {"enabled": True, "commands": []}}, change, [{"check": "required_command", "result": "requested", "command_id": "missing"}], tmp_path, enabled=True)
    assert missing[0]["diagnostic"]["code"] == "invalid_policy"


def test_verifier_does_not_use_a_shell() -> None:
    source = Path(__file__).resolve().parents[1].joinpath("dep_intel/review_verify.py").read_text(encoding="utf-8")
    assert "shell=True" not in source
    assert "shell=False" in source


def _config(command: list[str], *, timeout: int = 30, cwd: str = ".", required: bool = True) -> dict:
    return {
        "verification": {
            "enabled": True,
            "timeout_seconds": timeout,
            "allow_network": False,
            "commands": [{"id": "unit", "cwd": cwd, "command": command, "paths": ["**"], "required": required}],
        }
    }
