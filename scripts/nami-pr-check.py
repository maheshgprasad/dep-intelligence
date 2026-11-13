#!/usr/bin/env python3
"""Print a Nami Gate check for a stored review and exit 1 on block.

Warn and pass stay green. The evidence package path is printed when it already exists.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

from dep_intel.config import Settings  # noqa: E402
from dep_intel.release_ops import check_summary  # noqa: E402
from dep_intel.review_store import read_review  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Nami Gate check for a stored review")
    parser.add_argument("--review-id", required=True)
    args = parser.parse_args()
    settings = Settings.load()
    report = read_review(settings.output_dir, args.review_id)
    if report is None:
        print(f"No stored review {args.review_id}")
        return 1
    summary = check_summary(report)
    print(summary["text"])
    package = settings.output_dir / "reviews" / args.review_id / "exports" / "evidence.zip"
    if package.is_file():
        print(f"Evidence package: {package}")
    decision = summary["decision"]
    if decision == "block":
        print(f"::error::{summary['text'].splitlines()[0]}")
    elif decision == "warn":
        print(f"::warning::{summary['text'].splitlines()[0]}")
    return int(summary["exit_code"])


if __name__ == "__main__":
    sys.exit(main())
