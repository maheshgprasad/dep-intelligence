"""repos.txt is the source of truth. cluster-manifest.json is generated from it."""

from __future__ import annotations

import json
from pathlib import Path

from dep_intel.cluster_manifest import ClusterManifest, load_manifest
from dep_intel.cluster_manifest_generation import (
    configuration_hash,
    prepare_repository_configuration,
    render_manifest,
)
from dep_intel.config import Settings
from dep_intel.cross_repo_graph import index_snapshot, published_dict, snapshot_from_published
from dep_intel.graph_models import CONSUMES_HTTP, HTTP_CONTRACT, GraphEdge, GraphNode, Partition, symbol_key
from dep_intel.repo_config import RepoConfigError, load_repo_config, parse_repo_config
from dep_intel.service_clusters import cluster_downstream, confirmed_relationships, detect_service_clusters
from dep_intel.sources import load_repos

ROOT = Path(__file__).resolve().parents[2]


def _settings(root: Path, text: str) -> Settings:
    repos = root / "repos.txt"
    repos.write_text(text, encoding="utf-8")
    return Settings(root=root, repos_file=repos, output_dir=root / "out", github_token="", ghe_token="")


def test_legacy_lines_keep_flat_repos_and_ignore_comments(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        "\n# comment\n\nfixtures/auth-service\nhttps://github.com/example/repo.git\n",
    )
    config = load_repo_config(settings)
    assert config.explicit is False
    assert config.clusters == []
    assert [item.service_id for item in config.repositories] == ["auth_service", "example_repo"]
    assert load_repos(settings)[0].kind == "local"
    assert load_repos(settings)[1].kind == "github"
    assert load_repos(settings)[1].repo == "repo"


def test_explicit_clusters_standalone_paths_and_urls(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        """
# comment

[cluster:example-platform]
api-gateway = https://github.com/example/api-gateway
auth-service = ../services/auth-service

[standalone]
utility = /tmp/utility
""",
    )
    config = load_repo_config(settings)
    assert config.explicit is True
    assert config.clusters[0].id == "example-platform"
    assert config.clusters[0].service_ids == ["api-gateway", "auth-service"]
    assert config.repositories[0].repo.kind == "github"
    assert config.repositories[1].repo.kind == "local"
    assert config.repositories[2].standalone is True
    assert config.repositories[2].cluster_id is None
    assert [item.service_id for item in load_repo_config(settings).repositories] == [
        item.service_id for item in config.repositories
    ]
    assert [item.raw for item in load_repos(settings)] == [item.repo.raw for item in config.repositories]


def test_validation_reports_the_repos_txt_line(tmp_path: Path) -> None:
    cases = {
        "[cluster:example-platform]\napi = https://github.com/example/api\nauth = https://github.com/example/auth\n\n[cluster:other]\napi = https://github.com/example/api-b\nbilling = https://github.com/example/billing\n": "cannot belong to both clusters",
        "[cluster:example-platform]\napi = https://github.com/example/api\nauth = https://github.com/example/api\n": "duplicate repository",
        "[cluster:example-platform]\napi = https://github.com/example/api\nauth = https://github.com/example/auth\n\n[cluster:other]\nbilling = https://github.com/example/api\nother = https://github.com/example/other\n": "cannot belong to both clusters",
        "[cluster:bad id]\napi = https://github.com/example/api\nauth = https://github.com/example/auth\n": "invalid cluster id",
        "[cluster:example-platform]\napi = https://github.com/example/api\n": "at least two services",
        "[cluster:example-platform]\n": "empty cluster",
        "[cluster:example-platform]\napi https://github.com/example/api\nauth = https://github.com/example/auth\n": "malformed assignment",
        "[cluster:example-platform]\napi =\nauth = https://github.com/example/auth\n": "missing repository reference",
        "[cluster:example-platform]\napi = https://github.com/example/api\napi = https://github.com/example/other\n": "duplicate service id",
    }
    for text, expected in cases.items():
        try:
            parse_repo_config(text, tmp_path / "repos.txt", tmp_path)
        except RepoConfigError as exc:
            assert any(expected in error and "repos.txt:" in error for error in exc.errors), exc.errors
        else:
            raise AssertionError(expected)


def test_generated_manifest_is_deterministic_and_updates_when_repos_change(tmp_path: Path) -> None:
    text = "[cluster:example-platform]\napi-gateway = https://github.com/example/api-gateway\nauth-service = https://github.com/example/auth-service\n\n[standalone]\nutility = https://github.com/example/utility\n"
    settings = _settings(tmp_path, text)
    first = prepare_repository_configuration(settings, target=tmp_path / "cluster-manifest.json")
    assert first.wrote is True
    body = (tmp_path / "cluster-manifest.json").read_bytes()
    second = prepare_repository_configuration(settings, target=tmp_path / "cluster-manifest.json")
    assert second.unchanged is True
    assert (tmp_path / "cluster-manifest.json").read_bytes() == body
    assert render_manifest(first.config) == render_manifest(load_repo_config(settings))
    other = tmp_path / "other"
    other.mkdir()
    (other / "cluster-manifest.json").write_text(render_manifest(load_repo_config(settings)), encoding="utf-8")
    assert (other / "cluster-manifest.json").read_bytes() == body
    _settings(tmp_path, text.replace("utility", "worker"))
    updated = prepare_repository_configuration(settings, target=tmp_path / "cluster-manifest.json")
    assert updated.wrote is True
    payload = json.loads((tmp_path / "cluster-manifest.json").read_text(encoding="utf-8"))
    assert payload["schema_version"] == 2
    assert payload["generated"]["source"] == "repos.txt"
    assert payload["generated"]["source_hash"] == configuration_hash(updated.config)
    assert "generated_at" not in payload["generated"]
    assert payload["clusters"] == [{"id": "example-platform", "services": ["api-gateway", "auth-service"]}]
    assert {item["id"] for item in payload["services"]} == {"api-gateway", "auth-service", "worker"}
    assert all(item["repository"].startswith("https://github.com/example/") for item in payload["services"])
    repos = load_repos(settings)
    manifest, errors = load_manifest(tmp_path / "cluster-manifest.json", repos, tmp_path)
    assert errors == [] and manifest is not None
    assert [service.id for service in manifest.services] == ["api-gateway", "auth-service", "worker"]


def test_schema_v1_still_loads_and_v2_cluster_rules_fail_clearly(tmp_path: Path) -> None:
    repos_file = tmp_path / "repos.txt"
    repos_file.write_text("fixtures/auth-service\nfixtures/user-service\n", encoding="utf-8")
    settings = Settings(root=ROOT, repos_file=repos_file, output_dir=tmp_path, github_token="", ghe_token="")
    repos = load_repos(settings)
    legacy = {
        "schema_version": 1,
        "services": [
            {"id": "auth-service", "repository": "fixtures/auth-service"},
            {"id": "user-service", "repository": "fixtures/user-service"},
        ],
    }
    path = tmp_path / "cluster-manifest.json"
    path.write_text(json.dumps(legacy), encoding="utf-8")
    manifest, errors = load_manifest(path, repos, ROOT)
    assert errors == [] and manifest is not None and manifest.clusters == []
    broken = {
        "schema_version": 2,
        "clusters": [{"id": "example-platform", "services": ["auth-service"]}],
        "services": [
            {"id": "auth-service", "repository": "fixtures/auth-service"},
            {"id": "user-service", "repository": "fixtures/user-service"},
        ],
    }
    path.write_text(json.dumps(broken), encoding="utf-8")
    manifest, errors = load_manifest(path, repos, ROOT)
    assert manifest is None and errors


def test_user_managed_manifest_is_not_replaced_until_requested(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        "[cluster:example-platform]\napi = https://github.com/example/api\nauth = https://github.com/example/auth\n",
    )
    path = tmp_path / "cluster-manifest.json"
    original = json.dumps({"schema_version": 1, "services": [{"id": "api", "repository": "https://github.com/example/api"}]})
    path.write_text(original, encoding="utf-8")
    kept = prepare_repository_configuration(settings, target=path)
    assert kept.wrote is False and kept.notices
    assert "will not overwrite" in kept.notices[0]
    assert path.read_text(encoding="utf-8") == original
    replaced = prepare_repository_configuration(settings, target=path, replace_existing=True)
    assert replaced.wrote is True
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 2


def test_declared_cluster_survives_missing_and_cross_cluster_edges() -> None:
    snapshot = _snapshot(
        ["api", "auth", "billing", "ledger", "utility"],
        [("api", "billing")],
        [
            {"cluster_id": "payments", "services": ["api", "auth"], "membership_source": "declared"},
            {"cluster_id": "finance", "services": ["billing", "ledger"], "membership_source": "declared"},
        ],
    )
    groups = detect_service_clusters(snapshot)
    assert [item["cluster_id"] for item in groups["clusters"]] == ["payments", "finance"]
    assert groups["clusters"][0]["services"] == ["api", "auth"]
    assert groups["clusters"][0]["relationship_count"] == 0
    assert groups["clusters"][0]["membership_source"] == "declared"
    assert {item["service_id"] for item in groups["standalone"]} == {"utility"}
    edge = next(row for row in confirmed_relationships(snapshot))
    assert edge["from_service"] == "api" and edge["to_service"] == "billing" and edge["cross_cluster"] is True
    impact = cluster_downstream(snapshot, "billing")
    assert impact["cluster_id"] == "finance"
    assert "api" not in impact["services"]
    assert impact["services"] == []


def test_legacy_connected_components_remain_without_a_declaration() -> None:
    snapshot = _snapshot(["auth-service", "user-service", "tooling-repo"], [("auth-service", "user-service")], None)
    groups = detect_service_clusters(snapshot)
    assert groups["membership_source"] == "inferred"
    assert groups["clusters"][0]["services"] == ["auth-service", "user-service"]
    assert groups["clusters"][0]["relationship_count"] == 1
    assert [item["service_id"] for item in groups["standalone"]] == ["tooling-repo"]


def test_published_snapshot_keeps_declared_clusters() -> None:
    declared = [{"cluster_id": "example-platform", "services": ["api", "auth"], "membership_source": "declared"}]
    snapshot = _snapshot(["api", "auth"], [], declared)
    restored = snapshot_from_published(published_dict(snapshot))
    assert restored.declared_clusters == declared
    assert detect_service_clusters(restored)["clusters"][0]["services"] == ["api", "auth"]
    assert detect_service_clusters(restored)["clusters"][0]["relationship_count"] == 0


def _snapshot(services: list[str], calls: list[tuple[str, str]], declared):
    partitions = {
        service: Partition(service_id=service, repository=service, workspace_head=f"head-{service}", quality="ok")
        for service in services
    }
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
        version="g-declared",
        generated_at="2026-01-01T00:00:00+00:00",
        partitions=partitions,
        overlay={},
        bridges=edges,
        diagnostics=[],
        fingerprints=[],
        declared_clusters=declared,
    )


def test_topology_alias_becomes_a_contract_host(tmp_path: Path) -> None:
    from dep_intel.cluster_manifest import ClusterManifest
    from dep_intel.contracts import _host_index

    (tmp_path / "nami-topology.yaml").write_text(
        "aliases:\n  billing:\n    service: uocp-billing-service\n",
        encoding="utf-8",
    )
    manifest = ClusterManifest.model_validate({
        "schema_version": 2,
        "services": [
            {"id": "uocp-billing-service", "repository": "https://github.com/example/billing"},
            {"id": "uocp-tenant-service", "repository": "https://github.com/example/tenant"},
        ],
    })
    assert _host_index(manifest, tmp_path)["billing"] == ["uocp-billing-service"]


def test_cluster_manifest_model_round_trip_uses_defaults() -> None:
    manifest = ClusterManifest.model_validate({
        "schema_version": 2,
        "generated": {
            "generated_by": "nami-trace",
            "source": "repos.txt",
            "source_hash": "sha256:abc",
            "membership": "declared",
        },
        "clusters": [{"id": "example-platform", "services": ["api", "auth"]}],
        "services": [
            {"id": "api", "repository": "https://github.com/example/api"},
            {"id": "auth", "repository": "https://github.com/example/auth"},
        ],
    })
    assert manifest.impact.threshold == 0.1
    assert manifest.clusters[0].services == ["api", "auth"]
