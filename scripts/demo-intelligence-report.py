#!/usr/bin/env python3
"""Generate workspace, service-cluster, and repository reports.

Uses the current snapshot when one is published, then always runs a small
deterministic scope fixture: auth → user → reporting, plus standalone tooling.
Does not edit fixtures or repos.txt.
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
from dep_intel.service_clusters import detect_service_clusters  # noqa: E402


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
    documents = []
    try:
        documents.append(generate_report(output, snapshot, "workspace"))
        groups = detect_service_clusters(snapshot)
        if groups["clusters"]:
            documents.append(generate_report(output, snapshot, "service_cluster", cluster_id=groups["clusters"][0]["cluster_id"]))
            print(f"Service cluster {groups['clusters'][0]['cluster_id']}: {groups['clusters'][0]['service_count']} services, {groups['clusters'][0]['relationship_count']} calls, {len(groups['standalone'])} standalone")
        else:
            print("No connected service cluster in this snapshot.")
        service = next(iter(snapshot.partitions))
        documents.append(generate_report(output, snapshot, "repository", service))
    finally:
        engine.close()
    for document in documents:
        report_id = document["report_id"]
        print(f"Report: {report_id}")
        print(f"Scope: {document['scope']}")
        print(f"Services: {(document.get('workspace') or {}).get('service_count') or 1}")
        print(f"Graph version: {document['reproducibility']['graph_version']}")
        print(f"PDF: {ensure_export(output.output_dir, report_id, 'pdf')}")
        print(f"JSON: {ensure_export(output.output_dir, report_id, 'json')}")
        print(f"Package: {ensure_export(output.output_dir, report_id, 'zip')}")
    _print_scope_fixture()
    return 0


def _print_scope_fixture() -> None:
    directory = ROOT / "output" / "demo-report-scope"
    directory.mkdir(parents=True, exist_ok=True)
    settings = Settings(root=directory, repos_file=directory / "repos.txt", output_dir=directory, github_token="", ghe_token="")
    snapshot = _scope_fixture()
    groups = detect_service_clusters(snapshot)
    workspace = generate_report(settings, snapshot, "workspace")
    cluster = generate_report(settings, snapshot, "service_cluster", cluster_id=groups["clusters"][0]["cluster_id"])
    repository = generate_report(settings, snapshot, "repository", "tooling-repo")
    print("Scope fixture")
    print(f"Workspace services: {workspace['workspace']['service_count']}")
    print(f"Service cluster services: {cluster['workspace']['service_count']}")
    print(f"Standalone: {[item['service_id'] for item in groups['standalone']]}")
    print(f"Repository report: {repository['identity']['service']}")


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


def _scope_fixture():
    """auth-service → user-service → reporting-service, with tooling-repo standalone."""
    partitions = {}
    edges = []
    chain = [("auth-service", "user-service"), ("user-service", "reporting-service")]
    for service in ("auth-service", "user-service", "reporting-service", "tooling-repo"):
        partitions[service] = Partition(service_id=service, repository=service, workspace_head="fixture", quality="ok")
    for source, target in chain:
        identity = f"http::GET:/{target}"
        contract = GraphNode(
            key=symbol_key(target, "", identity, HTTP_CONTRACT),
            category=HTTP_CONTRACT,
            service_id=target,
            name=f"GET /{target}",
            qualified_name=identity,
            file_path="src/app.py",
            kind=HTTP_CONTRACT,
            metadata={"identity": identity},
        )
        caller = GraphNode(
            key=symbol_key(source, "src/app.py", f"{source}::call", "Function"),
            category="code_symbol",
            service_id=source,
            name="call",
            qualified_name=f"{source}::call",
            file_path="src/app.py",
            kind="Function",
        )
        partitions[target].nodes[contract.key] = contract
        partitions[source].nodes[caller.key] = caller
        edges.append(GraphEdge(caller.key, contract.key, CONSUMES_HTTP, {"status": "declared", "resolution": "exact"}))
    return index_snapshot(
        version="demo-scope",
        generated_at="2026-01-01T00:00:00+00:00",
        partitions=partitions,
        overlay={},
        bridges=edges,
        diagnostics=[],
        fingerprints=[],
    )


if __name__ == "__main__":
    raise SystemExit(main())
