#!/usr/bin/env python3
"""Evaluate labelled Boundary Gate scenarios and write the metrics artifact."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

from dep_intel.gate_evaluation import evaluate_corpus, format_table, write_evaluation  # noqa: E402


def main() -> int:
    payload = evaluate_corpus()
    path = write_evaluation(payload, ROOT / "output")
    print(format_table(payload))
    print(f"artifact: {path}")
    return 0 if not payload["failures"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
