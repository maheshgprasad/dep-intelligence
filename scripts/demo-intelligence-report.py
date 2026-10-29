#!/usr/bin/env python3
"""Generate a cluster report and a repository report from published intelligence.

Uses the current snapshot when one is published. Otherwise builds an in-memory
fixture snapshot. Does not edit fixtures or repos.txt.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

from dep_intel.cluster_service import get_engine  # noqa: E402
from dep_intel.config import Settings  # noqa: E402
from dep_intel.cross_repo_graph import index_snapshot  # noqa: E402
from dep_intel.graph_models import CONSUMES_HTTP, HTTP_CONTRACT, GraphEdge, GraphNode, Partition, symbol_key  # noqa: E402
from dep_intel.reporting.builder import generate_report  # noqa: E402
from dep_intel.reporting.package import ensure_export  # noqa: E402


def main() -> int:
    settings = Settings.load()
    engine = get_engine(settings)
    snapshot = engine.current()
    output = settings
    if snapshot is None or not snapshot.version:
        output = Settings(
            root=ROOT / "output" / "demo-intelligence-report",
            repos_file=settings.repos_file,
            output_dir=ROOT / "output" / "demo-intelligence-report",
            github_token="",
            ghe_token="",
        )
        output.output_dir.mkdir(parents=True, exist_ok=True)
        snapshot = _fixture_snapshot()
        print("No published snapshot. Using an in-memory fixture graph.")
    else:
        print(f"Published graph {snapshot.version}")
    try:
        cluster = generate_report(output, snapshot, "cluster")
        service = next(iter(snapshot.partitions))
        repository = generate_report(output, snapshot, "repository", service)
    finally:
        engine.close()
    for document in (cluster, repository):
        report_id = document["report_id"]
        print(f"Report: {report_id}")
        print(f"Scope: {document['scope']}")
        print(f"Graph version: {document['reproducibility']['graph_version']}")
        print(f"PDF: {ensure_export(output.output_dir, report_id, 'pdf')}")
        print(f"JSON: {ensure_export(output.output_dir, report_id, 'json')}")
        print(f"Package: {ensure_export(output.output_dir, report_id, 'zip')}")
    return 0


def _fixture_snapshot():
    identity = "http::POST:/api/auth/validate"
    contract = GraphNode(
        key=symbol_key("auth-service", "", identity, HTTP_CONTRACT),
        category=HTTP_CONTRACT,
        service_id="auth-service",
        name="POST /api/auth/validate",
        qualified_name=identity,
        file_path="src/server.js",
        kind=HTTP_CONTRACT,
        metadata={"identity": identity},
    )
    caller = GraphNode(
        key=symbol_key("user-service", "src/users.js", "src/users.js::listUsers", "Function"),
        category="code_symbol",
        service_id="user-service",
        name="listUsers",
        qualified_name="src/users.js::listUsers",
        file_path="src/users.js",
        kind="Function",
    )
    auth = Partition(service_id="auth-service", repository="fixtures/auth-service", workspace_head="fixture")
    user = Partition(service_id="user-service", repository="fixtures/user-service", workspace_head="fixture")
    auth.nodes[contract.key] = contract
    user.nodes[caller.key] = caller
    edge = GraphEdge(caller.key, contract.key, CONSUMES_HTTP, {"status": "extracted"})
    return index_snapshot(
        version="demo-intelligence",
        generated_at="2026-01-01T00:00:00+00:00",
        partitions={"auth-service": auth, "user-service": user},
        overlay={},
        bridges=[edge],
        diagnostics=[],
        fingerprints=[],
    )


if __name__ == "__main__":
    raise SystemExit(main())
