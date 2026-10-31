#!/usr/bin/env python3
"""Show independent Gate validation for a consumed authentication route removal."""

from __future__ import annotations

import runpy
from pathlib import Path

raise SystemExit(runpy.run_path(str(Path(__file__).resolve().with_name("demo-evidence-gate.py")), run_name="__main__"))
