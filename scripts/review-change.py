#!/usr/bin/env python3
"""Run Nami Boundary Gate from the command line.

Exit status:
  0  pass or warn
  1  block
  2  incomplete, or the review could not be executed
"""

from __future__ import annotations

import argparse
import json
import sys

from dep_intel.cluster_service import ClusterEngine
from dep_intel.config import Settings
from dep_intel.review_engine import ReviewEngine


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Nami Boundary Gate on a change.")
    parser.add_argument("--repository", default="")
    parser.add_argument("--service", default="")
    parser.add_argument("--working-tree", action="store_true")
    parser.add_argument("--staged", action="store_true")
    parser.add_argument("--commit", default="")
    parser.add_argument("--base", default="")
    parser.add_argument("--head", default="")
    parser.add_argument("--pr-url", default="")
    parser.add_argument("--fast", action="store_true")
    parser.add_argument("--exact", action="store_true")
    parser.add_argument("--verify", dest="verify", action="store_true", default=True)
    parser.add_argument("--no-verify", dest="verify", action="store_false")
    parser.add_argument("--model", dest="model", action="store_true")
    parser.add_argument("--no-model", dest="model", action="store_false")
    parser.add_argument("--json", dest="as_json", action="store_true")
    args = parser.parse_args()
    kind = "working_tree"
    if args.staged:
        kind = "staged"
    elif args.commit:
        kind = "commit"
    elif args.base or args.head:
        kind = "range"
    elif args.pr_url:
        kind = "github_pr"
    elif args.working_tree:
        kind = "working_tree"
    settings = Settings.load()
    cluster = ClusterEngine(settings)
    try:
        report = ReviewEngine(settings, cluster=cluster).run(
            {
                "source": {
                    "kind": kind,
                    "repository": args.repository,
                    "base_ref": args.base,
                    "head_ref": args.head,
                    "commit": args.commit,
                    "pr_url": args.pr_url,
                    "service_id": args.service,
                },
                "service": args.service,
                "mode": "exact" if args.exact and not args.fast else "fast",
                "verify": args.verify,
                "model": {"enabled": bool(args.model)},
            }
        )
    finally:
        cluster.close()
    decision = (report.get("gate") or {}).get("decision") or "incomplete"
    if args.as_json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(f"Decision: {decision.upper()}")
        print(report.get("summary", {}).get("text") or "")
        print(f"Review: {report.get('review_id')}")
    if decision in {"pass", "warn"}:
        return 0
    if decision == "block":
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
