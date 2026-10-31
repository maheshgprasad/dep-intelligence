#!/usr/bin/env python3
"""Generate a workspace, service-cluster, or repository intelligence report.

By default this uses the current snapshot and does not refresh graphs.
Pass --refresh to rebuild the cluster snapshot first, then report that version.
--cluster is a deprecated alias for --workspace.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

from dep_intel.cluster_service import get_engine  # noqa: E402
from dep_intel.config import Settings  # noqa: E402
from dep_intel.reporting.builder import ReportError, generate_report  # noqa: E402
from dep_intel.reporting.package import ensure_export  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a Nami intelligence report from published intelligence.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--workspace", action="store_true", help="Report every repository in the published snapshot.")
    group.add_argument("--cluster", action="store_true", help="Deprecated alias for --workspace.")
    group.add_argument("--service-cluster", default="", help="Report one confirmed service cluster by id.")
    group.add_argument("--repository", default="")
    parser.add_argument("--format", choices=["pdf", "json", "markdown", "evidence"], default="")
    parser.add_argument("--refresh", action="store_true", help="Refresh the cluster snapshot before generating.")
    args = parser.parse_args()
    settings = Settings.load()
    engine = get_engine(settings)
    try:
        if args.refresh:
            refreshed = engine.refresh()
            print(f"Refresh: {refreshed.get('status')} {refreshed.get('version') or ''}")
            if refreshed.get("status") != "ok":
                return 2
        snapshot = engine.current()
        if args.repository:
            scope, service, cluster_id = "repository", args.repository, ""
        elif args.service_cluster:
            scope, service, cluster_id = "service_cluster", "", args.service_cluster
        else:
            scope, service, cluster_id = "workspace", "", ""
        document = generate_report(settings, snapshot, scope, service, cluster_id=cluster_id)
    except ReportError as exc:
        print(exc)
        return 2
    finally:
        engine.close()
    report_id = document["report_id"]
    version = document["reproducibility"]["graph_version"]
    print(f"Report: {report_id}")
    print(f"Scope: {document['scope']}")
    print(f"Graph version: {version}")
    kinds = [args.format] if args.format else ["markdown", "json", "pdf", "evidence"]
    mapping = {"evidence": "zip", "pdf": "pdf", "json": "json", "markdown": "markdown"}
    for kind in kinds:
        path = ensure_export(settings.output_dir, report_id, mapping[kind])
        print(f"{kind}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
