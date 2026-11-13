"""Review impact lists confirmed cluster consumers without inventing file ranks."""

from __future__ import annotations

from dep_intel.cross_repo_graph import index_snapshot
from dep_intel.graph_models import CONSUMES_HTTP, HTTP_CONTRACT, GraphEdge, GraphNode, Partition, symbol_key
from dep_intel.service_clusters import cluster_downstream


def test_cluster_downstream_includes_direct_and_transitive_consumers() -> None:
    snapshot = _chain()
    impact = cluster_downstream(snapshot, "reporting-service")
    assert impact["cluster_id"]
    assert impact["services"] == ["user-service", "auth-service"]
    by_service = {item["service_id"]: item for item in impact["items"]}
    assert by_service["user-service"]["direct"] is True
    assert by_service["user-service"]["contract"]
    assert by_service["auth-service"]["direct"] is False
    assert by_service["auth-service"]["via"] == "user-service"


def test_caller_in_a_cluster_has_no_downstream_consumer() -> None:
    impact = cluster_downstream(_chain(), "auth-service")
    assert impact["cluster_id"]
    assert impact["services"] == []
    assert impact["items"] == []


def test_standalone_repository_is_not_given_cluster_impact() -> None:
    impact = cluster_downstream(_chain(), "tooling-repo")
    assert impact == {"cluster_id": "", "services": [], "items": []}


def _chain():
    services = ["auth-service", "user-service", "reporting-service", "tooling-repo"]
    partitions = {
        service: Partition(service_id=service, repository=service, workspace_head=f"head-{service}", quality="ok")
        for service in services
    }
    edges = []
    for source, target in (("auth-service", "user-service"), ("user-service", "reporting-service")):
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
        edges.append(GraphEdge(caller.key, contract.key, CONSUMES_HTTP, {"status": "extracted", "resolution": "exact"}))
    return index_snapshot(
        version="g-chain",
        generated_at="2026-01-01T00:00:00+00:00",
        partitions=partitions,
        overlay={},
        bridges=edges,
        diagnostics=[],
        fingerprints=[],
    )
