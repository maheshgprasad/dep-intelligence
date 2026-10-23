"""Hybrid structural/temporal impact: alignment, cutoff, ranking, and fusion."""

from __future__ import annotations

import inspect
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from dep_intel.api import cluster_hybrid_impact, cluster_impact
from dep_intel.cluster_manifest import ImpactSettings
from dep_intel.cluster_service import ClusterEngine
from dep_intel.commits import _history_reports, analyze_commits, history_window_start, lagged_support, temporal_rank_score
from dep_intel.config import PROJECT_ROOT, Settings
from dep_intel.evidence_bridge import CanonicalFileRef, canonical_file_ref, file_backed_chain, lookup_file_nodes, normalize_relative_path
from dep_intel.graph_models import (
    CODE_SYMBOL,
    CONSUMES_HTTP,
    HANDLED_BY,
    HTTP_CONTRACT,
    GraphEdge,
    GraphNode,
    Partition,
    symbol_key,
)
from dep_intel.hybrid_impact import HybridSettings, analyze_hybrid, _hybrid_score
from dep_intel.impact import analyze
from dep_intel.mcp_server import analyze_hybrid_impact
from dep_intel.cross_repo_graph import index_snapshot
from dep_intel.sources import RepoRef, Workspace
from dep_intel.temporal_providers import MemoryTemporalProvider, structure_conditioned_lag


ROOT = PROJECT_ROOT


def _node(service: str, name: str, *, kind: str = "Function", category: str = CODE_SYMBOL, file: str = "src/a.js", line: int = 1) -> GraphNode:
    qualified = f"{file}::{name}" if file else name
    return GraphNode(
        key=symbol_key(service, file, qualified, kind),
        category=category,
        service_id=service,
        name=name,
        qualified_name=qualified,
        file_path=file,
        line_start=line,
        line_end=line + 3,
        kind=kind,
        bounds_verified=True,
    )


def _contract(service: str, method: str, path: str) -> GraphNode:
    identity = f"http::{method}:{path}"
    return GraphNode(
        key=symbol_key(service, "", identity, HTTP_CONTRACT),
        category=HTTP_CONTRACT,
        service_id=service,
        name=f"{method} {path}",
        qualified_name=identity,
        file_path="",
        kind=HTTP_CONTRACT,
        metadata={"identity": identity},
    )


def _edge(origin: GraphNode, target: GraphNode, relation: str) -> GraphEdge:
    return GraphEdge(origin.key, target.key, relation, {"status": "extracted", "source": "crg", "confidence": 1.0})


def _snapshot(nodes: list[GraphNode], edges: list[GraphEdge], version: str = "v1"):
    partitions: dict[str, Partition] = {}
    for node in nodes:
        part = partitions.setdefault(node.service_id, Partition(service_id=node.service_id, repository=node.service_id))
        part.nodes[node.key] = node
    return index_snapshot(
        version=version,
        generated_at="2026-01-01T00:00:00+00:00",
        partitions=partitions,
        overlay={},
        bridges=edges,
        diagnostics=[],
        fingerprints=[],
    )


def _workspace(slug: str, name: str) -> Workspace:
    ref = RepoRef(raw=name, name=name, slug=slug, kind="local", path=Path("/tmp") / slug)
    return Workspace(ref=ref, files={})


def test_absolute_and_relative_paths_share_canonical_ref(tmp_path: Path) -> None:
    repo = tmp_path / "auth-service"
    (repo / "src").mkdir(parents=True)
    relative = _node("auth-service", "validate", file="src/server.js")
    absolute = _node("auth-service", "validate", file=str(repo / "src" / "server.js"))
    snapshot = _snapshot([relative], [])
    left = canonical_file_ref(relative, snapshot)
    right = canonical_file_ref(absolute, snapshot, repo_root=repo)
    assert left is not None and right is not None
    assert left.relative_file_path == right.relative_file_path == "src/server.js"
    windows = normalize_relative_path("C:\\auth-service\\src\\server.js", repository="auth-service")
    dotted = normalize_relative_path("./src//server.js")
    assert windows == "src/server.js"
    assert dotted == "src/server.js"


def test_lookup_does_not_join_on_basename() -> None:
    shallow = _node("auth", "one", file="src/a.js")
    nested = _node("auth", "two", file="src/lib/a.js")
    snapshot = _snapshot([shallow, nested], [])
    ref = CanonicalFileRef("auth", "auth", "src/a.js")
    found = lookup_file_nodes(snapshot, ref)
    assert {node.name for node in found} == {"one"}


def test_contract_without_file_has_no_canonical_identity() -> None:
    handler = _node("auth", "validate", file="src/server.js")
    contract = _contract("auth", "POST", "/api/auth/validate")
    client = _node("user", "listUsers", file="src/users.js")
    snapshot = _snapshot(
        [handler, contract, client],
        [_edge(contract, handler, HANDLED_BY), _edge(client, contract, CONSUMES_HTTP)],
    )
    assert canonical_file_ref(contract, snapshot) is None
    result = analyze(snapshot, [handler.key], mode="code_change", settings=ImpactSettings())
    item = next(row for row in result.affected if row.name == "listUsers")
    chain = file_backed_chain(snapshot, handler.key, item.path)
    assert [ref.relative_file_path for _node, ref in chain] == ["src/server.js", "src/users.js"]
    assert all(node.category != HTTP_CONTRACT for node, _ref in chain)


def test_directional_same_repo_rates_differ() -> None:
    workspace = _workspace("svc", "svc")
    log = "\n".join(
        [
            "commit a|2026-01-01T00:00:00+00:00|A",
            "1\t0\tsrc/a.js",
            "1\t0\tsrc/b.js",
            "commit b|2026-01-02T00:00:00+00:00|A",
            "1\t0\tsrc/a.js",
            "commit c|2026-01-03T00:00:00+00:00|A",
            "1\t0\tsrc/b.js",
        ]
    )
    _cochange, temporal = _history_reports(workspace, log, 365)
    rates = {(item["source_file"], item["target_file"]): item["directional_rate"] for item in temporal["pairs"]}
    assert rates[("src/a.js", "src/b.js")] == pytest.approx(0.5)
    assert rates[("src/b.js", "src/a.js")] == pytest.approx(0.5)
    a_only = _history_reports(
        workspace,
        "\n".join(
            [
                "commit a|2026-01-01T00:00:00+00:00|A",
                "1\t0\tsrc/a.js",
                "1\t0\tsrc/b.js",
                "commit b|2026-01-02T00:00:00+00:00|A",
                "1\t0\tsrc/a.js",
                "commit c|2026-01-03T00:00:00+00:00|A",
                "1\t0\tsrc/a.js",
            ]
        ),
        365,
    )[1]
    rates = {(item["source_file"], item["target_file"]): item["directional_rate"] for item in a_only["pairs"]}
    assert rates[("src/a.js", "src/b.js")] == pytest.approx(1 / 3, abs=1e-4)
    assert rates[("src/b.js", "src/a.js")] == pytest.approx(1.0)
    coupling = next(item["coupling"] for item in _cochange["cochange"] if {item["file_a"], item["file_b"]} == {"src/a.js", "src/b.js"})
    assert coupling == pytest.approx(0.5)


def test_causal_cutoff_excludes_future_commits() -> None:
    provider = MemoryTemporalProvider(
        {
            "auth": [
                {"committed_at": "2026-01-01T00:00:00+00:00", "files": ["src/a.js", "src/b.js"]},
                {"committed_at": "2026-06-01T00:00:00+00:00", "files": ["src/a.js", "src/b.js"]},
            ]
        },
        {"auth": "auth"},
    )
    source = CanonicalFileRef("auth", "auth", "src/a.js")
    target = CanonicalFileRef("auth", "auth", "src/b.js")
    before = provider.pair_evidence(source, target, "2026-02-01T00:00:00+00:00")
    after = provider.pair_evidence(source, target, None)
    assert before.support == 1
    assert after.support == 2
    assert before.last_supported_at == "2026-01-01T00:00:00+00:00"


def test_same_repository_cochange_and_mining_until(tmp_path: Path) -> None:
    repo = tmp_path / "svc"
    repo.mkdir()
    (repo / "src").mkdir()
    (repo / "src" / "a.js").write_text("a\n", encoding="utf-8")
    (repo / "src" / "b.js").write_text("b\n", encoding="utf-8")
    _git(repo, "init")
    (repo / "src" / "a.js").write_text("a1\n", encoding="utf-8")
    (repo / "src" / "b.js").write_text("b1\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(
        repo,
        "-c",
        "user.email=t@example.com",
        "-c",
        "user.name=T",
        "commit",
        "-m",
        "pair",
        env={"GIT_AUTHOR_DATE": "2026-01-10T00:00:00+00:00", "GIT_COMMITTER_DATE": "2026-01-10T00:00:00+00:00"},
    )
    (repo / "src" / "a.js").write_text("future\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(
        repo,
        "-c",
        "user.email=t@example.com",
        "-c",
        "user.name=T",
        "commit",
        "-m",
        "future",
        env={"GIT_AUTHOR_DATE": "2026-08-01T00:00:00+00:00", "GIT_COMMITTER_DATE": "2026-08-01T00:00:00+00:00"},
    )
    settings = Settings(root=ROOT, repos_file=tmp_path / "repos.txt", output_dir=tmp_path / "out", github_token="", ghe_token="")
    ref = RepoRef(raw=str(repo), name="svc", slug="svc", kind="local", path=repo)
    workspace = Workspace(ref=ref, files={"src/a.js": "a", "src/b.js": "b"})
    with httpx.Client() as client:
        analyze_commits([workspace], settings, client, since_days=0, until="2026-02-01T00:00:00+00:00")
    temporal = json.loads((tmp_path / "out" / "graphs" / "svc" / "temporal_evidence.json").read_text(encoding="utf-8"))
    cochange = json.loads((tmp_path / "out" / "graphs" / "svc" / "cochange.json").read_text(encoding="utf-8"))
    assert "coupling" in cochange["cochange"][0]
    assert temporal["history"]["until"] is not None
    stamps = [event["committed_at"] for event in temporal["events"] if event.get("committed_at")]
    assert stamps and all(stamp <= "2026-02-01T00:00:00+00:00" for stamp in stamps)
    assert any("src/a.js" in event["files"] and "src/b.js" in event["files"] for event in temporal["events"])


def test_cross_repo_lag_requires_structural_path() -> None:
    handler = _node("auth", "validate", file="src/server.js")
    contract = _contract("auth", "POST", "/api/auth/validate")
    client = _node("user", "listUsers", file="src/users.js")
    unrelated = _node("user", "other", file="src/other.js")
    snapshot = _snapshot(
        [handler, contract, client, unrelated],
        [_edge(contract, handler, HANDLED_BY), _edge(client, contract, CONSUMES_HTTP)],
    )
    provider = MemoryTemporalProvider(
        {
            "auth": [{"committed_at": "2026-01-01T00:00:00+00:00", "files": ["src/server.js"]}],
            "user": [
                {"committed_at": "2026-01-02T00:00:00+00:00", "files": ["src/users.js"]},
                {"committed_at": "2026-01-02T00:00:00+00:00", "files": ["src/other.js"]},
            ],
        },
        {"auth": "auth", "user": "user"},
    )
    structural = analyze(snapshot, [handler.key], mode="code_change", settings=ImpactSettings())
    hybrid = analyze_hybrid(
        snapshot,
        structural,
        provider,
        settings=HybridSettings(expand_temporal=False),
        as_of="2026-01-10T00:00:00+00:00",
        provider_name="memory",
    )
    users = next(row for row in hybrid["candidates"] if row["name"] == "listUsers")
    other_keys = [row["name"] for row in hybrid["candidates"]]
    assert "other" not in other_keys
    kinds = {item["evidence_kind"] for item in users["temporal"]["temporal_path_evidence"]}
    assert "structure_conditioned_lagged_history" in kinds
    blob = json.dumps(users)
    assert "these two files co-changed" not in blob
    assert users["temporal"]["evidence_kind"] == "structure_conditioned_lagged_history"
    lag = structure_conditioned_lag(
        provider,
        CanonicalFileRef("auth", "auth", "src/server.js"),
        CanonicalFileRef("user", "user", "src/users.js"),
        "2026-01-10T00:00:00+00:00",
        7,
        90,
    )
    assert lag.evidence_kind == "structure_conditioned_lagged_history"
    assert "co-changed" not in lag.observation


def test_missing_temporal_keeps_structural_candidate() -> None:
    handler = _node("auth", "validate", file="src/server.js")
    contract = _contract("auth", "POST", "/api/auth/validate")
    client = _node("user", "listUsers", file="src/users.js")
    snapshot = _snapshot(
        [handler, contract, client],
        [_edge(contract, handler, HANDLED_BY), _edge(client, contract, CONSUMES_HTTP)],
    )
    structural = analyze(snapshot, [handler.key], mode="code_change", settings=ImpactSettings())
    scores = {item.name: item.score for item in structural.affected}
    hybrid = analyze_hybrid(
        snapshot,
        structural,
        MemoryTemporalProvider({}, {"auth": "auth", "user": "user"}),
        settings=HybridSettings(expand_temporal=False),
        provider_name="memory",
    )
    users = next(row for row in hybrid["candidates"] if row["name"] == "listUsers")
    assert users["evidence_origin"] == "structural_only"
    assert users["structural"]["score"] == pytest.approx(scores["listUsers"])
    assert users["hybrid_score"] == pytest.approx(0.7 * scores["listUsers"])


def test_temporal_only_union_and_experiments() -> None:
    changed = _node("auth", "validate", file="src/server.js")
    hidden = _node("auth", "helper", file="src/hidden.js")
    snapshot = _snapshot([changed, hidden], [])
    structural = analyze(snapshot, [changed.key], mode="code_change", settings=ImpactSettings())
    provider = MemoryTemporalProvider(
        {
            "auth": [
                {"committed_at": "2026-01-01T00:00:00+00:00", "files": ["src/server.js", "src/hidden.js"]},
                {"committed_at": "2026-01-02T00:00:00+00:00", "files": ["src/server.js", "src/hidden.js"]},
                {"committed_at": "2026-01-03T00:00:00+00:00", "files": ["src/server.js", "src/hidden.js"]},
            ]
        },
        {"auth": "auth"},
    )
    hybrid = analyze_hybrid(
        snapshot,
        structural,
        provider,
        settings=HybridSettings(expand_temporal=True, temporal_candidate_limit=25, experiment="hybrid"),
        as_of="2026-01-10T00:00:00+00:00",
        provider_name="memory",
    )
    hidden_row = next(row for row in hybrid["candidates"] if row["name"] == "helper")
    assert hidden_row["evidence_origin"] == "temporal_only"
    structural_only = analyze_hybrid(
        snapshot,
        structural,
        provider,
        settings=HybridSettings(expand_temporal=True, experiment="structural_only"),
        as_of="2026-01-10T00:00:00+00:00",
        provider_name="memory",
    )
    assert all(row["structural"]["available"] for row in structural_only["candidates"])
    temporal_only = analyze_hybrid(
        snapshot,
        structural,
        provider,
        settings=HybridSettings(expand_temporal=True, experiment="temporal_only"),
        as_of="2026-01-10T00:00:00+00:00",
        provider_name="memory",
    )
    assert all(row["temporal"]["available"] for row in temporal_only["candidates"])


def test_structural_score_unchanged_and_hybrid_formula() -> None:
    handler = _node("auth", "validate", file="src/server.js")
    contract = _contract("auth", "POST", "/api/auth/validate")
    client = _node("user", "listUsers", file="src/users.js")
    snapshot = _snapshot(
        [handler, contract, client],
        [_edge(contract, handler, HANDLED_BY), _edge(client, contract, CONSUMES_HTTP)],
    )
    structural = analyze(snapshot, [handler.key], mode="code_change", settings=ImpactSettings())
    before = {item.key: item.score for item in structural.affected}
    hybrid = analyze_hybrid(
        snapshot,
        structural,
        MemoryTemporalProvider({}, {"auth": "auth", "user": "user"}),
        settings=HybridSettings(expand_temporal=False),
        provider_name="memory",
    )
    after = {item.key: item.score for item in structural.affected}
    assert before == after
    users = next(row for row in hybrid["candidates"] if row["name"] == "listUsers")
    assert users["structural"]["score"] == pytest.approx(0.8)
    assert users["structural"]["score_kind"] == "structural_impact"
    assert _hybrid_score(0.7, 0.3, 0.80, 0.35, True, True, "hybrid") == pytest.approx(0.665)
    ranked = temporal_rank_score(0.417, "2026-01-01T00:00:00+00:00", 5, as_of=datetime(2026, 1, 19, tzinfo=timezone.utc))
    assert ranked["score_kind"] == "temporal_evidence"
    assert ranked["score"] == pytest.approx(0.417 * ranked["recency"] * ranked["support_factor"])
    assert 0 <= ranked["score"] <= 1


def test_deterministic_tie_break() -> None:
    left = _node("auth", "a", file="src/a.js")
    right = _node("auth", "b", file="src/b.js")
    snapshot = _snapshot([left, right], [_edge(right, left, "CALLS"), _edge(left, right, "CALLS")])
    structural = analyze(snapshot, [left.key], mode="code_change", settings=ImpactSettings())
    hybrid = analyze_hybrid(
        snapshot,
        structural,
        MemoryTemporalProvider({}, {"auth": "auth"}),
        settings=HybridSettings(expand_temporal=False),
        provider_name="memory",
    )
    keys = [row["key"] for row in hybrid["candidates"]]
    again = analyze_hybrid(
        snapshot,
        structural,
        MemoryTemporalProvider({}, {"auth": "auth"}),
        settings=HybridSettings(expand_temporal=False),
        provider_name="memory",
    )
    assert keys == [row["key"] for row in again["candidates"]]
    scores = [(row["hybrid_score"], row["structural"]["score"], row["temporal"]["score"], row["service_id"], row["file_path"], row["sort_key"]) for row in hybrid["candidates"]]
    assert scores == sorted(scores, key=lambda item: (-item[0], -item[1], -item[2], item[3], item[4], item[5]))


def test_http_and_mcp_share_hybrid_engine() -> None:
    assert ".hybrid_impact" in inspect.getsource(cluster_hybrid_impact)
    assert "engine.hybrid_impact" in inspect.getsource(analyze_hybrid_impact)
    assert "analyze_hybrid" in inspect.getsource(ClusterEngine.hybrid_impact)
    assert ".impact" in inspect.getsource(cluster_impact)
    assert "analyze_hybrid" not in inspect.getsource(ClusterEngine.impact)


def test_future_history_does_not_leak_into_as_of_hybrid() -> None:
    changed = _node("auth", "validate", file="src/server.js")
    neighbour = _node("auth", "helper", file="src/hidden.js")
    snapshot = _snapshot([changed, neighbour], [])
    structural = analyze(snapshot, [changed.key], mode="code_change", settings=ImpactSettings())
    provider = MemoryTemporalProvider(
        {
            "auth": [
                {"committed_at": "2026-09-01T00:00:00+00:00", "files": ["src/server.js", "src/hidden.js"]},
            ]
        },
        {"auth": "auth"},
    )
    hybrid = analyze_hybrid(
        snapshot,
        structural,
        provider,
        settings=HybridSettings(expand_temporal=True, experiment="hybrid"),
        as_of="2026-01-01T00:00:00+00:00",
        provider_name="memory",
    )
    assert all(row["name"] != "helper" for row in hybrid["candidates"])
    assert hybrid["temporal_cutoff"] == "2026-01-01T00:00:00+00:00"
    assert hybrid["module_version"] == "2.0"
    assert hybrid["ranking_unit"] == "file"
    assert hybrid["causal_validity"]["structural"] is False
    assert hybrid["root"]["file_path"] == "src/server.js"
    assert all(row["file_path"] != "src/server.js" for row in hybrid["candidates"])


def test_history_window_anchors_to_cutoff() -> None:
    cutoff = datetime(2025, 1, 1, tzinfo=timezone.utc)
    assert history_window_start(365, cutoff) == "2024-01-02"
    assert history_window_start(0, cutoff) == ""
    naive = datetime(2025, 1, 1)
    assert history_window_start(365, naive) == "2024-01-02"
    date_only = datetime(2025, 1, 1, tzinfo=timezone.utc)
    assert history_window_start(1, date_only) == "2024-12-31"
    start = history_window_start(365, None, now_at=datetime(2026, 10, 23, tzinfo=timezone.utc))
    assert start == "2025-10-23"


def test_one_to_one_lag_does_not_reuse_target() -> None:
    day = datetime(2026, 1, 1, tzinfo=timezone.utc)
    sources = [day, day + timedelta(days=1), day + timedelta(days=2)]
    targets = [day + timedelta(days=3)]
    stats = lagged_support(sources, targets, timedelta(days=7), matching="one_to_one")
    reusable = lagged_support(sources, targets, timedelta(days=7), matching="reusable_target")
    assert stats["support"] == 1
    assert reusable["support"] == 3


def _git(repo: Path, *args: str, env: dict[str, str] | None = None) -> None:
    import os
    import subprocess

    merged = {**os.environ, **(env or {})}
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=merged)
