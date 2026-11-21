"""Declared cluster membership is configuration. Topology evidence does not redefine it."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dep_intel.cluster_manifest import ClusterManifest, load_manifest
from dep_intel.cluster_manifest_generation import ensure_manifest, manifest_bytes, source_hash
from dep_intel.config import Settings
from dep_intel.cross_repo_graph import index_snapshot, published_dict, snapshot_from_published
from dep_intel.graph_models import CONSUMES_HTTP, HTTP_CONTRACT, GraphEdge, GraphNode, Partition, symbol_key
from dep_intel.service_clusters import cluster_downstream, detect_service_clusters
from dep_intel.sources import RepoConfigError, load_repos, parse_repos_text

ROOT = Path(__file__).resolve().parents[2]


def test_sections_keep_service_id_cluster_and_checkout_slug(tmp_path: Path) -> None:
    text = "\n".join([
        "# comment",
        "",
        "[standalone]",
        "tool = https://github.com/example/tool",
        "local-tool = fixtures/auth-service",
        "[cluster:payments]",
        "api = https://github.com/acme/api",
        "worker = fixtures/user-service",
        "legacy-line",
    ])
    loaded = parse_repos_text(text.replace("legacy-line", "fixtures/report-job"), ROOT)
    by_id = {item.service_id: item for item in loaded}
    assert by_id["tool"].cluster == ""
    assert by_id["tool"].slug == "example_tool"
    assert by_id["tool"].raw == "https://github.com/example/tool"
    assert by_id["local-tool"].kind == "local"
    assert by_id["local-tool"].slug == "local_tool"
    assert by_id["local-tool"].cluster == ""
    assert by_id["api"].cluster == "payments"
    assert by_id["api"].slug == "acme_api"
    assert by_id["worker"].cluster == "payments"
    legacy = next(item for item in loaded if item.raw == "fixtures/report-job")
    assert legacy.cluster == "payments"
    assert legacy.service_id == "report-job"
    assert legacy.slug == "report_job"


def test_legacy_lines_have_no_cluster() -> None:
    loaded = parse_repos_text("fixtures/auth-service\nhttps://github.com/example/repo\n", ROOT)
    assert [item.cluster for item in loaded] == ["", ""]
    assert loaded[0].kind == "local"
    assert loaded[1].kind == "github"
    assert loaded[1].service_id == "example_repo"


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        ("auth = https://github.com/acme/a\nauth = https://github.com/acme/b\n", "duplicate service id"),
        ("a = https://github.com/acme/same\nb = https://github.com/acme/same\n", "duplicate repository"),
        ("[cluster:bad id]\na = https://github.com/acme/a\nb = https://github.com/acme/b\n", "invalid cluster id"),
        ("[cluster:solo]\nonly = https://github.com/acme/only\n", "at least two"),
        ("not an assignment =\n", "malformed assignment"),
        ("auth =\n", "malformed assignment"),
    ],
)
def test_repository_configuration_is_rejected(text: str, fragment: str) -> None:
    with pytest.raises(RepoConfigError) as caught:
        parse_repos_text(text, ROOT)
    assert fragment in str(caught.value)
    assert ":" in caught.value.errors[0]


def test_schema_v1_loads_with_empty_clusters(tmp_path: Path) -> None:
    path = tmp_path / "cluster-manifest.json"
    path.write_text(json.dumps({
        "schema_version": 1,
        "services": [{"id": "auth-service", "repository": "fixtures/auth-service"}],
    }), encoding="utf-8")
    repos = parse_repos_text("fixtures/auth-service\n", ROOT)
    manifest, errors = load_manifest(path, repos, ROOT)
    assert errors == []
    assert manifest is not None
    assert manifest.schema_version == 1
    assert manifest.clusters == []


def test_generation_is_deterministic_and_preserves_bindings(tmp_path: Path) -> None:
    repos = tmp_path / "repos.txt"
    repos.write_text("\n".join([
        "[cluster:payments]",
        "auth = https://github.com/acme/auth",
        "billing = https://github.com/acme/billing",
        "[standalone]",
        "tool = https://github.com/acme/tool",
    ]), encoding="utf-8")
    previous = tmp_path / "cluster-manifest.json"
    previous.write_text(json.dumps({
        "schema_version": 1,
        "services": [{
            "id": "auth",
            "repository": "https://github.com/acme/auth",
            "http": {"host_aliases": ["auth.internal"]},
            "expected": [{"kind": "http", "consumer": "billing", "provider": "auth", "method": "GET", "path": "/v1"}],
        }],
    }), encoding="utf-8")
    settings = Settings(root=tmp_path, repos_file=repos, output_dir=tmp_path / "out", github_token="", ghe_token="")
    first = ensure_manifest(settings)
    assert first == previous
    body = json.loads(previous.read_text(encoding="utf-8"))
    assert body["schema_version"] == 2
    assert body["clusters"] == [{"id": "payments", "services": ["auth", "billing"]}]
    auth = next(item for item in body["services"] if item["id"] == "auth")
    assert auth["http"]["host_aliases"] == ["auth.internal"]
    assert auth["expected"][0]["provider"] == "auth"
    assert "tool" in {item["id"] for item in body["services"]}
    assert body["generated"]["source_hash"] == source_hash(load_repos(settings))
    first_bytes = previous.read_bytes()
    assert ensure_manifest(settings) == previous
    assert previous.read_bytes() == first_bytes
    assert manifest_bytes(ClusterManifest.model_validate(body)) == first_bytes
    repos.write_text(repos.read_text(encoding="utf-8").replace("tool =", "tooling ="), encoding="utf-8")
    ensure_manifest(settings)
    updated = json.loads(previous.read_text(encoding="utf-8"))
    assert updated["generated"]["source_hash"] != body["generated"]["source_hash"]
    assert "tooling" in {item["id"] for item in updated["services"]}
    aliases = (tmp_path / "nami-topology.yaml").read_text(encoding="utf-8")
    assert "auth.internal" in aliases
    assert auth["http"]["host_aliases"] == ["auth.internal"]


def test_declared_cluster_keeps_a_service_with_no_call() -> None:
    snapshot = _services(("A", "B", "C"), [("A", "B")], [{"id": "group", "services": ["A", "B", "C"]}])
    groups = detect_service_clusters(snapshot)
    assert groups["membership_source"] == "declared"
    assert groups["clusters"][0]["services"] == ["A", "B", "C"]
    assert groups["clusters"][0]["cluster_id"] == "group"
    assert "C" not in {item["service_id"] for item in groups["standalone"]}


def test_cross_cluster_edge_does_not_merge_or_enter_downstream() -> None:
    declared = [
        {"id": "one", "services": ["A", "B"]},
        {"id": "two", "services": ["C", "D"]},
    ]
    snapshot = _services(("A", "B", "C", "D"), [("A", "B"), ("B", "C"), ("C", "D")], declared)
    groups = detect_service_clusters(snapshot)
    assert [item["services"] for item in groups["clusters"]] == [["A", "B"], ["C", "D"]]
    edge = next(row for row in __import__("dep_intel.service_clusters", fromlist=["confirmed_relationships"]).confirmed_relationships(snapshot) if row["from_service"] == "B")
    assert edge["cross_cluster"] is True
    assert edge["from_cluster"] == "one"
    assert edge["to_cluster"] == "two"
    downstream = cluster_downstream(snapshot, "B")
    assert "C" not in downstream["services"]
    assert any(item["service_id"] == "C" and item["cross_cluster"] is True for item in downstream["external"])


def test_legacy_connected_components_are_unchanged() -> None:
    snapshot = _services(("A", "B", "C"), [("A", "B")], [])
    groups = detect_service_clusters(snapshot)
    assert groups["membership_source"] == "derived"
    assert groups["clusters"][0]["services"] == ["A", "B"]
    assert {item["service_id"] for item in groups["standalone"]} == {"C"}


def test_published_snapshot_keeps_declarations() -> None:
    snapshot = _services(("A", "B", "C"), [("A", "B")], [{"id": "group", "services": ["A", "B", "C"]}])
    restored = snapshot_from_published(published_dict(snapshot))
    assert restored.declared_clusters == snapshot.declared_clusters
    assert detect_service_clusters(restored)["clusters"][0]["services"] == ["A", "B", "C"]


def _services(names: tuple[str, ...], calls: list[tuple[str, str]], declared: list[dict]) :
    partitions = {name: Partition(service_id=name, repository=name) for name in names}
    edges = []
    for source, target in calls:
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
        version="declared",
        generated_at="2026-01-01T00:00:00+00:00",
        partitions=partitions,
        overlay={},
        bridges=edges,
        diagnostics=[],
        fingerprints=[],
        declared_clusters=declared,
    )
