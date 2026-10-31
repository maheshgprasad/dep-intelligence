#!/usr/bin/env python3
"""Discover service topology from the allowlist. Does not guess providers from route names."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

from dep_intel.config import Settings  # noqa: E402
from dep_intel.topology.discovery import discover, load_effective  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Discover Nami service topology.")
    parser.add_argument("command", choices=["discover", "show"])
    args = parser.parse_args()
    settings = Settings.load()
    payload = discover(settings) if args.command == "discover" else load_effective(settings)
    if payload is None:
        print("No discovered topology. Run: python scripts/nami-topology.py discover")
        return 1
    print(json.dumps({
        "services": len(payload.get("services") or []),
        "relationships": len(payload.get("relationships") or []),
        "unresolved": len(payload.get("unresolved") or []),
        "overrides": len(payload.get("overrides") or []),
    }, indent=2))
    for item in (payload.get("relationships") or [])[:12]:
        print(f"{item.get('from')} -> {item.get('to')} {item.get('method')} {item.get('path')}")
    for item in (payload.get("unresolved") or [])[:12]:
        print(f"unresolved {item.get('from')} {item.get('method')} {item.get('path')} {item.get('host')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
