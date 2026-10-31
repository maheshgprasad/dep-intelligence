#!/usr/bin/env python3
"""Fail when backend line coverage drops below the recorded floor.

Coverage measures executed lines. It does not establish semantic correctness.
"""

from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def main() -> int:
    coverage_path = Path(sys.argv[1] if len(sys.argv) > 1 else "output/ci/coverage.xml")
    baseline_path = Path(sys.argv[2] if len(sys.argv) > 2 else ".github/quality-baseline.json")
    root = ET.parse(coverage_path).getroot()
    measured = float(root.get("line-rate") or 0) * 100
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    required = float(baseline["backend_line_coverage_min"])
    status = "PASS" if measured + 1e-9 >= required else "FAIL"
    print(f"Backend line coverage: {measured:.1f}%")
    print(f"Required minimum: {required:.1f}%")
    print(f"Status: {status}")
    print("Coverage measures executed lines. It does not establish semantic correctness.")
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
