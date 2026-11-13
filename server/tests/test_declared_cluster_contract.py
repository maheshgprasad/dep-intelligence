"""Semantic contract for declared repository clusters."""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from dep_intel.cluster_manifest import load_manifest
from dep_intel.cluster_manifest_generation import prepare_repository_configuration, render_manifest
from dep_intel.config import Settings
from dep_intel.repo_config import load_repo_config
from dep_intel.review_gate import decide_gate
from dep_intel.service_clusters import detect_service_clusters
from dep_intel.sources import load_repos
from dep_intel.topology.merge import effective
from tests.test_repo_config import _settings, _snapshot

pytestmark = pytest.mark.semantic_contract


def test_decl_001_explicit_membership_is_authoritative() -> None:
    snapshot = _snapshot(
        ["api", "auth"],
        [],
        [{"cluster_id": "example-platform", "services": ["api", "auth"], "membership_source": "declared"}],
    )
    groups = detect_service_clusters(snapshot)
    assert groups["membership_source"] == "declared"
    assert groups["clusters"][0]["services"] == ["api", "auth"]
    assert groups["clusters"][0]["relationship_count"] == 0


def test_decl_002_topology_evidence_does_not_change_declared_membership() -> None:
    before = _snapshot(
        ["api", "auth", "billing"],
        [],
        [{"cluster_id": "example-platform", "services": ["api", "auth"], "membership_source": "declared"}],
    )
    after = _snapshot(
        ["api", "auth", "billing"],
        [("auth", "billing")],
        before.declared_clusters,
    )
    assert detect_service_clusters(before)["clusters"][0]["services"] == detect_service_clusters(after)["clusters"][0]["services"]
    assert "billing" not in detect_service_clusters(after)["clusters"][0]["services"]


def test_decl_003_cross_cluster_relationships_do_not_merge_clusters() -> None:
    snapshot = _snapshot(
        ["api", "auth", "billing", "ledger"],
        [("auth", "billing")],
        [
            {"cluster_id": "example-platform", "services": ["api", "auth"], "membership_source": "declared"},
            {"cluster_id": "example-finance", "services": ["billing", "ledger"], "membership_source": "declared"},
        ],
    )
    groups = detect_service_clusters(snapshot)
    assert [item["services"] for item in groups["clusters"]] == [["api", "auth"], ["billing", "ledger"]]


def test_decl_004_generated_manifest_is_deterministic(tmp_path: Path) -> None:
    settings = _settings(tmp_path, "[cluster:example-platform]\napi = https://github.com/example/api\nauth = https://github.com/example/auth\n")
    config = load_repo_config(settings)
    assert render_manifest(config) == render_manifest(load_repo_config(settings))
    first = prepare_repository_configuration(settings, target=tmp_path / "a.json")
    second = prepare_repository_configuration(settings, target=tmp_path / "b.json")
    assert first.wrote and second.wrote
    assert (tmp_path / "a.json").read_bytes() == (tmp_path / "b.json").read_bytes()


def test_decl_005_legacy_repos_txt_remains_supported(tmp_path: Path) -> None:
    settings = _settings(tmp_path, "# legacy\nfixtures/auth-service\nhttps://github.com/example/repo\n")
    config = load_repo_config(settings)
    assert config.explicit is False
    assert [item.repo.raw for item in config.repositories] == ["fixtures/auth-service", "https://github.com/example/repo"]
    assert load_repos(settings)[0].name == "auth-service"


def test_decl_006_and_007_generated_services_match_the_allowlist(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        "[cluster:example-platform]\napi = https://github.com/example/api\nauth = https://github.com/example/auth\n\n[standalone]\nutility = https://github.com/example/utility\n",
    )
    result = prepare_repository_configuration(settings, target=tmp_path / "cluster-manifest.json")
    repos = load_repos(settings)
    manifest, errors = load_manifest(result.path, repos, tmp_path)
    assert errors == [] and manifest is not None
    declared = {item.repo.raw for item in result.config.repositories}
    assert {service.repository for service in manifest.services} == declared
    assert len(manifest.services) == len(repos)
    assert len({service.id for service in manifest.services}) == len(manifest.services)


def test_decl_008_standalone_repositories_stay_outside_declared_clusters(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        "[cluster:example-platform]\napi = https://github.com/example/api\nauth = https://github.com/example/auth\n\n[standalone]\nutility = https://github.com/example/utility\n",
    )
    config = load_repo_config(settings)
    members = {service_id for cluster in config.clusters for service_id in cluster.service_ids}
    standalone = [item.service_id for item in config.repositories if item.standalone]
    assert standalone == ["utility"]
    assert set(standalone).isdisjoint(members)


def test_decl_009_topology_discovery_is_not_cluster_assignment() -> None:
    merged = effective({"services": [], "calls": [], "aliases": []}, Path("."), [])
    assert merged["schema"] == "nami.topology.effective"
    assert "clusters" not in merged
    assert "service_clusters" not in merged


def test_decl_010_boundary_gate_does_not_read_cluster_configuration() -> None:
    names = set(inspect.signature(decide_gate).parameters)
    assert "cluster" not in names
    assert "manifest" not in names
    assert "declared_clusters" not in inspect.getsource(decide_gate)
