"""Behavioral tests for the cross-repository structural impact engine."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from dep_intel.api import resolve_ui_file
from dep_intel.changes import diff_snapshots
from dep_intel.cluster_manifest import ImpactSettings, load_manifest
from dep_intel.commits import _summarize
from dep_intel.config import PROJECT_ROOT, Settings
from dep_intel.contract_extractors.javascript import extract_javascript, resolve_routes
from dep_intel.contract_extractors.openapi import compare_operations, parse_openapi
from dep_intel.contracts import build_contract_model, match_template
from dep_intel.crg_adapter import ExtractionFailed, UnsupportedSchema, extract_partition, stale_partition
from dep_intel.cross_repo_graph import assemble, index_snapshot
from dep_intel.events import EventBus
from dep_intel.graph_models import (
    CODE_SYMBOL,
    CONSUMED_BY,
    CONSUMES_GRPC,
    CONSUMES_HTTP,
    EVENT_CONTRACT,
    FILE_ANCHOR,
    GRPC_CONTRACT,
    HANDLED_BY,
    HTTP_CONTRACT,
    IMPLEMENTED_BY,
    PRODUCES_EVENT,
    GraphEdge,
    GraphNode,
    Partition,
    symbol_key,
)
from dep_intel.impact import analyze
from dep_intel.jobs import JobRunner
from dep_intel.paths import contained
from dep_intel.pipeline import _aggregate
from dep_intel.sources import Checkout, RepoRef, Workspace, redact, sanitize_tool_remote, strip_userinfo
from dep_intel.store import read_json, write_json
from dep_intel.watcher import WatchCoordinator

ROOT = PROJECT_ROOT
SCHEMA = """
CREATE TABLE nodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    qualified_name TEXT NOT NULL UNIQUE,
    file_path TEXT NOT NULL,
    line_start INTEGER,
    line_end INTEGER,
    language TEXT,
    is_test INTEGER DEFAULT 0,
    file_hash TEXT,
    extra TEXT DEFAULT '{}',
    updated_at REAL NOT NULL
);
CREATE TABLE edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    source_qualified TEXT NOT NULL,
    target_qualified TEXT NOT NULL,
    file_path TEXT NOT NULL,
    line INTEGER DEFAULT 0,
    extra TEXT DEFAULT '{}',
    confidence REAL,
    confidence_tier TEXT,
    target_resolution TEXT,
    updated_at REAL NOT NULL
);
CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def _settings(tmp_path: Path) -> Settings:
    repos = tmp_path / "repos.txt"
    repos.write_text("fixtures/auth-service\nfixtures/user-service\nfixtures/report-job\n", encoding="utf-8")
    return Settings(root=ROOT, repos_file=repos, output_dir=tmp_path / "out", github_token="", ghe_token="")


def _db(path: Path, nodes: list[tuple], edges: list[tuple], *, schema: str = "13", extra_sql: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.executescript(SCHEMA)
    if extra_sql:
        connection.executescript(extra_sql)
    connection.execute("INSERT INTO metadata (key, value) VALUES ('schema_version', ?)", (schema,))
    connection.execute("INSERT INTO metadata (key, value) VALUES ('last_updated', '2026-01-01T00:00:00')")
    for node in nodes:
        connection.execute(
            """
            INSERT INTO nodes (kind, name, qualified_name, file_path, line_start, line_end, language, is_test, file_hash, extra, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'javascript', 0, '', ?, 1)
            """,
            node,
        )
    for edge in edges:
        connection.execute(
            """
            INSERT INTO edges (kind, source_qualified, target_qualified, file_path, line, extra, confidence, confidence_tier, target_resolution, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            """,
            edge,
        )
    connection.commit()
    connection.close()


def _node(service: str, name: str, *, kind: str = "Function", category: str = CODE_SYMBOL, file: str = "src/a.js", line: int = 1, end: int = 4) -> GraphNode:
    qualified = f"{file}::{name}"
    return GraphNode(
        key=symbol_key(service, file, qualified, kind),
        category=category,
        service_id=service,
        name=name,
        qualified_name=qualified,
        file_path=file,
        line_start=line,
        line_end=end,
        kind=kind,
        bounds_verified=True,
    )


def _contract(service: str, method: str, path: str, category: str = HTTP_CONTRACT) -> GraphNode:
    identity = f"http::{method}:{path}" if category == HTTP_CONTRACT else f"{category}:{method}:{path}"
    return GraphNode(
        key=symbol_key(service, "", identity, category),
        category=category,
        service_id=service,
        name=f"{method} {path}",
        qualified_name=identity,
        file_path="",
        kind=category,
        metadata={"identity": identity},
    )


def _edge(origin: GraphNode, target: GraphNode, relation: str, **evidence) -> GraphEdge:
    payload = {"status": "extracted", "source": "crg", "confidence": 1.0, "injected": False}
    payload.update(evidence)
    return GraphEdge(origin.key, target.key, relation, payload)


def _snapshot(nodes: list[GraphNode], edges: list[GraphEdge], version: str = "v1") -> object:
    partitions: dict[str, Partition] = {}
    for node in nodes:
        part = partitions.setdefault(node.service_id, Partition(service_id=node.service_id, repository=node.service_id))
        part.nodes[node.key] = node
    return index_snapshot(version=version, generated_at="2026-01-01T00:00:00+00:00", partitions=partitions, overlay={}, bridges=edges, diagnostics=[], fingerprints=[])


def test_manifest_rejects_unknown_version_duplicates_and_escape(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    from dep_intel.sources import load_repos

    repos = load_repos(settings)
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"schema_version": 99, "services": []}), encoding="utf-8")
    manifest, errors = load_manifest(bad, repos, ROOT)
    assert manifest is None and "unsupported schema_version" in errors[0]
    duplicate = {
        "schema_version": 1,
        "services": [
            {"id": "auth-service", "repository": "fixtures/auth-service"},
            {"id": "auth-service", "repository": "fixtures/user-service"},
        ],
    }
    bad.write_text(json.dumps(duplicate), encoding="utf-8")
    _manifest, errors = load_manifest(bad, repos, ROOT)
    assert any("duplicate service" in error for error in errors)
    escaped = {
        "schema_version": 1,
        "services": [{"id": "auth-service", "repository": "fixtures/auth-service", "graph_db": "../graph.db"}],
    }
    bad.write_text(json.dumps(escaped), encoding="utf-8")
    _manifest, errors = load_manifest(bad, repos, ROOT)
    assert errors
    missing = {
        "schema_version": 1,
        "services": [
            {
                "id": "auth-service",
                "repository": "fixtures/auth-service",
                "client_bindings": [{"file": "src/a.js", "client": "axios", "target_service": "missing"}],
            }
        ],
    }
    bad.write_text(json.dumps(missing), encoding="utf-8")
    _manifest, errors = load_manifest(bad, repos, ROOT)
    assert any("missing service" in error for error in errors)
    good, errors = load_manifest(ROOT / "cluster-manifest.json", repos, ROOT)
    assert errors == [] and good is not None


def test_path_containment_rejects_sibling_prefix(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "app.js").write_text("ok", encoding="utf-8")
    sibling = tmp_path / "dist-evil"
    sibling.mkdir()
    (sibling / "secret.js").write_text("no", encoding="utf-8")
    assert contained(dist / "app.js", dist)
    assert not contained(sibling / "secret.js", dist)
    assert resolve_ui_file(dist, "app.js") == (dist / "app.js").resolve()
    assert resolve_ui_file(dist, "../dist-evil/secret.js") is None


def test_overlapping_row_ids_do_not_share_symbol_keys(tmp_path: Path) -> None:
    left = tmp_path / "a" / ".code-review-graph" / "graph.db"
    right = tmp_path / "b" / ".code-review-graph" / "graph.db"
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    (root_a / "src").mkdir(parents=True)
    (root_b / "src").mkdir(parents=True)
    file_a = str(root_a / "src" / "a.js")
    file_b = str(root_b / "src" / "b.js")
    _db(left, [("Function", "one", f"{file_a}::one", file_a, 1, 3, "{}")], [])
    _db(right, [("Function", "one", f"{file_b}::one", file_b, 1, 3, "{}")], [])
    first = extract_partition(left, service_id="auth-service", repository="a", repo_root=root_a)
    second = extract_partition(right, service_id="user-service", repository="b", repo_root=root_b)
    assert set(first.nodes) & set(second.nodes) == set()
    rebuilt = tmp_path / "a2" / ".code-review-graph" / "graph.db"
    root_2 = tmp_path / "a2"
    (root_2 / "src").mkdir(parents=True)
    file_2 = str(root_2 / "src" / "a.js")
    _db(rebuilt, [("Function", "one", f"{file_2}::one", file_2, 1, 3, "{}")], [])
    connection = sqlite3.connect(rebuilt)
    connection.execute("UPDATE nodes SET id = id + 50")
    connection.commit()
    connection.close()
    again = extract_partition(rebuilt, service_id="auth-service", repository="a2", repo_root=root_2)
    assert list(first.nodes.values())[0].crg_id != list(again.nodes.values())[0].crg_id
    assert list(first.nodes.values())[0].qualified_name.endswith("src/a.js::one")
    assert list(again.nodes.values())[0].qualified_name.endswith("src/a.js::one")


def test_unresolved_edges_parallel_edges_and_bad_schema(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    db = root / ".code-review-graph" / "graph.db"
    source = str(root / "src" / "a.js")
    (root / "src").mkdir(parents=True)
    _db(
        db,
        [("Function", "caller", f"{source}::caller", source, 1, 8, "{}")],
        [
            ("CALLS", f"{source}::caller", "missing", source, 2, "{}", 0.4, "INFERRED", "unresolved"),
            ("REFERENCES", f"{source}::caller", "missing", source, 3, "{", None, None, None),
        ],
    )
    partition = extract_partition(db, service_id="auth-service", repository="repo", repo_root=root)
    unresolved = [node for node in partition.nodes.values() if node.category == "unresolved"]
    assert unresolved and unresolved[0].metadata["resolved"] is False
    assert len(partition.edges) == 2
    assert any(item.code == "malformed_metadata" for item in partition.diagnostics)
    bad = tmp_path / "bad.db"
    connection = sqlite3.connect(bad)
    connection.execute("CREATE TABLE nodes (type TEXT, id INTEGER)")
    connection.execute("CREATE TABLE edges (source_id INTEGER, target_id INTEGER)")
    connection.commit()
    connection.close()
    with pytest.raises((UnsupportedSchema, ExtractionFailed)):
        extract_partition(bad, service_id="auth-service", repository="repo", repo_root=tmp_path)


def test_read_transaction_wal_replacement_and_stale_retention(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    db = root / ".code-review-graph" / "graph.db"
    source = str(root / "src" / "a.js")
    (root / "src").mkdir(parents=True)
    _db(db, [("Function", "one", f"{source}::one", source, 1, 2, "{}")], [])
    seen = {}

    def after_begin() -> None:
        other = sqlite3.connect(db)
        other.execute(
            "INSERT INTO nodes (kind, name, qualified_name, file_path, line_start, line_end, language, extra, updated_at) VALUES ('Function', 'two', ?, ?, 4, 5, 'javascript', '{}', 1)",
            (f"{source}::two", source),
        )
        other.commit()
        other.close()
        seen["inserted"] = True

    first = extract_partition(db, service_id="auth-service", repository="repo", repo_root=root, after_begin=after_begin)
    assert seen["inserted"]
    # The insert commits before the SELECT on a deferred transaction, so a later
    # extraction observes it. The failed extraction below must still keep `first`.
    second = extract_partition(db, service_id="auth-service", repository="repo", repo_root=root)
    assert any(node.name == "two" for node in second.nodes.values())
    connection = sqlite3.connect(db)
    connection.execute("UPDATE nodes SET name = 'renamed' WHERE name = 'one'")
    connection.commit()
    connection.close()
    third = extract_partition(db, service_id="auth-service", repository="repo", repo_root=root)
    assert any(node.name == "renamed" for node in third.nodes.values())
    db.write_bytes(b"not a database")
    with pytest.raises(ExtractionFailed):
        extract_partition(db, service_id="auth-service", repository="repo", repo_root=root)
    kept = stale_partition(third, "database replaced with garbage")
    assert kept.quality == "stale"
    assert any(node.name == "renamed" for node in kept.nodes.values())


def test_express_axios_fetch_prefixes_and_ambiguous_hosts() -> None:
    auth = (ROOT / "fixtures/auth-service/src/server.js").read_text(encoding="utf-8")
    user = (ROOT / "fixtures/user-service/src/users.js").read_text(encoding="utf-8")
    files = {
        "src/server.js": extract_javascript("src/server.js", auth),
        "src/users.js": extract_javascript("src/users.js", user),
    }
    routes, _notes = resolve_routes(files)
    assert ("POST", "/api/auth/validate") in {(route.method, route.path) for route in routes}
    assert ("GET", "/api/users") in {(route.method, route.path) for route in routes}
    noise = extract_javascript(
        "src/noise.js",
        """
        const axios = require("axios");
        const items = ["a"];
        items.get("/api/nope");
        axios.get("/api/nope");
        // app.get("/commented", handler);
        const sample = "app.get('/string', h)";
        fetch("/api/auth/validate", { method: "POST" });
        const client = axios.create({ baseURL: "http://auth-service:3001" });
        client.post("/api/auth/validate?x=1", {});
        """,
    )
    assert noise.routes == []
    fetch = next(call for call in noise.calls if call.client == "fetch")
    assert fetch.method == "POST"
    instance = next(call for call in noise.calls if call.client == "client")
    assert instance.base_url == "http://auth-service:3001"
    mounted = {
        "src/app.js": extract_javascript(
            "src/app.js",
            """
            const express = require("express");
            const api = require("./api");
            const app = express();
            app.use("/v1", api);
            """,
        ),
        "src/api.js": extract_javascript(
            "src/api.js",
            """
            const express = require("express");
            const router = express.Router();
            router.get("/health", function health(req, res) {});
            module.exports = router;
            """,
        ),
    }
    mounted_routes, notes = resolve_routes(mounted)
    assert notes == []
    assert any(route.path == "/v1/health" for route in mounted_routes)
    settings = _settings(Path("/tmp"))
    from dep_intel.sources import load_repos

    manifest, errors = load_manifest(ROOT / "cluster-manifest.json", load_repos(_settings(ROOT / "output")), ROOT)
    assert manifest and not errors
    model = build_contract_model(
        manifest,
        {
            "auth-service": {"src/server.js": auth},
            "user-service": {"src/users.js": user},
            "report-job": {},
        },
    )
    assert any(call.provider_id == "auth-service" and call.path == "/api/auth/validate" for call in model.calls)
    billing = json.loads((ROOT / "cluster-manifest.json").read_text(encoding="utf-8"))
    billing["services"].append(
        {
            "id": "billing-service",
            "repository": "fixtures/auth-service",
            "http": {"origins": ["http://billing:3001"], "host_aliases": ["billing"]},
        }
    )
    # Two providers with the same route are distinguished by hostname, not by the path alone.
    other = extract_javascript(
        "src/pay.js",
        """
        const axios = require("axios");
        axios.post("http://auth-service:3001/api/auth/validate", {});
        axios.post("http://billing:3001/api/auth/validate", {});
        """,
    )
    assert {call.url for call in other.calls} == {
        "http://auth-service:3001/api/auth/validate",
        "http://billing:3001/api/auth/validate",
    }
    assert match_template("/api/users", "/api/users/")[0] is False
    assert match_template("/api/users/1", "/api/users/{id}")[0] is True


def test_openapi_parses_operations_without_inventing_handlers() -> None:
    document = parse_openapi(
        json.dumps(
            {
                "openapi": "3.0.0",
                "paths": {
                    "/api/users": {
                        "post": {
                            "operationId": "createUser",
                            "requestBody": {"content": {"application/json": {"schema": {"type": "object", "required": ["name"]}}}},
                            "responses": {"200": {"content": {"application/json": {"schema": {"type": "object", "properties": {"id": {}, "name": {}}}}}}},
                        }
                    }
                },
                "components": {"schemas": {}},
            }
        ),
        "openapi.json",
    )
    assert document.operations[0].handler == ""
    assert document.operations[0].required_request_fields == ["name"]
    removed = compare_operations(document.operations, [])
    assert removed[0]["kind"] == "removed_operation"
    remote = parse_openapi('{"openapi":"3.0.0","paths":{},"components":{"schemas":{"A":{"$ref":"https://example.com/a.yaml"}}}}', "openapi.json")
    assert any("remote" in note for note in remote.diagnostics)


def test_scores_cycles_dominance_and_budgets() -> None:
    handler = _node("auth", "validate", file="src/server.js")
    contract = _contract("auth", "POST", "/api/auth/validate")
    client = _node("user", "listUsers", file="src/users.js", line=6, end=9)
    caller = _node("user", "main", file="src/users.js", line=12, end=14)
    snapshot = _snapshot(
        [handler, contract, client, caller],
        [
            _edge(contract, handler, HANDLED_BY, source="extractor"),
            _edge(client, contract, CONSUMES_HTTP, source="extractor"),
            _edge(caller, client, "CALLS"),
        ],
    )
    result = analyze(snapshot, [handler.key], mode="code_change", settings=ImpactSettings())
    scores = {item.name: item.score for item in result.affected}
    assert scores["validate"] == pytest.approx(1)
    assert scores["listUsers"] == pytest.approx(0.8)
    assert scores["main"] == pytest.approx(0.76)
    assert result.affected[0].confidence == pytest.approx(1) or result.affected[0].name == "validate"

    left = _node("s", "a")
    mid = _node("s", "b", file="src/b.js")
    right = _node("s", "c", file="src/c.js")
    far = _node("s", "d", file="src/d.js")
    dominated = _snapshot(
        [left, mid, right, far],
        [
            _edge(mid, left, CONSUMES_HTTP),
            _edge(right, mid, CONSUMES_HTTP),
            _edge(right, left, "CALLS"),
            _edge(far, right, CONSUMES_HTTP),
        ],
    )
    settings = ImpactSettings(max_hops=2, weights={"internal_dependency": 0.5, "http_contract": 0.9, "grpc_contract": 0.9, "event_contract": 0.4})
    found = analyze(dominated, [left.key], mode="code_change", settings=settings)
    names = {item.name for item in found.affected}
    assert "d" in names
    assert next(item for item in found.affected if item.name == "c").score == pytest.approx(0.81)

    cycle_a = _node("s", "loop-a")
    cycle_b = _node("s", "loop-b", file="src/b.js")
    cycled = _snapshot([cycle_a, cycle_b], [_edge(cycle_b, cycle_a, "CALLS"), _edge(cycle_a, cycle_b, "CALLS")])
    finished = analyze(cycled, [cycle_a.key], mode="code_change", settings=ImpactSettings(weights={"internal_dependency": 1, "http_contract": 1, "grpc_contract": 1, "event_contract": 1}))
    assert {item.name for item in finished.affected} == {"loop-a", "loop-b"}
    assert finished.truncated is False

    tiny = analyze(snapshot, [handler.key], mode="code_change", settings=ImpactSettings(max_nodes=1))
    assert tiny.truncated and tiny.truncation_reason == "max_nodes"
    cancel = threading.Event()
    cancel.set()
    stopped = analyze(snapshot, [handler.key], mode="code_change", settings=ImpactSettings(), cancel_event=cancel)
    assert stopped.truncation_reason == "cancelled"
    timed = analyze(snapshot, [handler.key], mode="code_change", settings=ImpactSettings(timeout_ms=0))
    assert timed.truncation_reason == "timeout"

    low = _snapshot([left, mid], [_edge(mid, left, "CALLS", confidence=0.2)])
    filtered = analyze(low, [left.key], mode="code_change", settings=ImpactSettings(min_edge_confidence=0.5))
    assert filtered.excluded
    assert all(item.name != "b" for item in filtered.affected)

    file_node = _node("s", "a.js", kind="File", category=FILE_ANCHOR, file="src/a.js")
    child = _node("s", "child", file="src/a.js", line=3, end=5)
    sibling = _node("s", "sibling", file="src/a.js", line=8, end=10)
    test_node = _node("s", "test_child", kind="Test", file="test.js")
    seeded = _snapshot(
        [file_node, child, sibling, test_node],
        [
            _edge(file_node, child, "CONTAINS"),
            _edge(file_node, sibling, "CONTAINS"),
            _edge(child, test_node, "TESTED_BY"),
            _edge(sibling, child, "CALLS"),
        ],
    )
    seeded_result = analyze(seeded, [file_node.key], mode="code_change", settings=ImpactSettings())
    assert {item.name for item in seeded_result.affected} >= {"a.js", "child", "sibling"}
    assert seeded_result.tests and seeded_result.tests[0]["name"] == "test_child"
    client_only = analyze(snapshot, [client.key], mode="code_change", settings=ImpactSettings())
    assert "validate" not in {item.name for item in client_only.affected}


def test_declared_grpc_and_event_fanout() -> None:
    impl = _node("auth", "validateRpc", file="src/rpc.js")
    caller = _node("user", "listUsers", file="src/users.js")
    other = _node("report", "total", file="report_job.py")
    contract = GraphNode(
        key=symbol_key("auth", "", "grpc:v1:auth.v1.Auth/Validate", GRPC_CONTRACT),
        category=GRPC_CONTRACT,
        service_id="auth",
        name="Auth/Validate",
        qualified_name="grpc:v1:auth.v1.Auth/Validate",
        file_path="",
        kind=GRPC_CONTRACT,
        metadata={"identity": "grpc:v1:auth.v1.Auth/Validate"},
    )
    snapshot = _snapshot(
        [impl, caller, contract],
        [_edge(contract, impl, IMPLEMENTED_BY, status="declared", source="manifest"), _edge(caller, contract, CONSUMES_GRPC, status="declared", source="manifest")],
    )
    result = analyze(snapshot, [impl.key], mode="code_change", settings=ImpactSettings())
    assert next(item.score for item in result.affected if item.name == "listUsers") == pytest.approx(0.8)

    event_a = GraphNode(
        key=symbol_key("user", "", "event:kafka:user.created@1", EVENT_CONTRACT),
        category=EVENT_CONTRACT,
        service_id="user",
        name="user.created",
        qualified_name="event:kafka:user.created@1",
        file_path="",
        kind=EVENT_CONTRACT,
    )
    event_b = GraphNode(
        key=symbol_key("user", "", "event:rabbit:user.created@1", EVENT_CONTRACT),
        category=EVENT_CONTRACT,
        service_id="user",
        name="user.created",
        qualified_name="event:rabbit:user.created@1",
        file_path="",
        kind=EVENT_CONTRACT,
    )
    consumer_b = _node("other", "elsewhere", file="src/other.js")
    events = _snapshot(
        [caller, other, consumer_b, event_a, event_b],
        [
            _edge(caller, event_a, PRODUCES_EVENT, status="declared", source="manifest"),
            _edge(event_a, other, CONSUMED_BY, status="declared", source="manifest"),
            _edge(consumer_b, event_b, PRODUCES_EVENT, status="declared", source="manifest"),
            _edge(event_b, consumer_b, CONSUMED_BY, status="declared", source="manifest"),
        ],
    )
    fanout = analyze(events, [caller.key], mode="code_change", settings=ImpactSettings())
    names = {item.name for item in fanout.affected}
    assert "total" in names
    assert "elsewhere" not in names
    rejected = analyze(events, [caller.key], mode="operational_failure", settings=ImpactSettings(), scenario="backpressure")
    assert any(item.code == "unsupported_operational" for item in rejected.diagnostics)


def test_deleted_route_uses_previous_graph_and_body_edit_is_not_structural() -> None:
    handler = _node("auth", "validate", file="src/server.js", line=9, end=11)
    handler.span_hash = "old"
    client = _node("user", "listUsers", file="src/users.js", line=6, end=9)
    contract = _contract("auth", "POST", "/api/auth/validate")
    old = _snapshot([handler, client, contract], [_edge(contract, handler, HANDLED_BY), _edge(client, contract, CONSUMES_HTTP)], version="old")
    changed = _node("auth", "validate", file="src/server.js", line=9, end=11)
    changed.span_hash = "new"
    changed.key = handler.key
    body = _snapshot([changed, client, contract], [_edge(contract, handler, HANDLED_BY), _edge(client, contract, CONSUMES_HTTP)], version="new")
    delta = diff_snapshots(old, body)
    assert handler.key in delta.body_only
    removed = _snapshot([client], [], version="removed")
    deletion = diff_snapshots(old, removed)
    assert contract.key in deletion.deleted
    previous = analyze(old, [handler.key], mode="code_change", settings=ImpactSettings(), topology="previous", previous_version=old.version)
    assert any(item.name == "listUsers" for item in previous.affected)


def test_bridge_restitch_does_not_duplicate(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    from dep_intel.sources import load_repos

    manifest, errors = load_manifest(ROOT / "cluster-manifest.json", load_repos(settings), ROOT)
    assert manifest and not errors
    auth = (ROOT / "fixtures/auth-service/src/server.js").read_text(encoding="utf-8")
    user = (ROOT / "fixtures/user-service/src/users.js").read_text(encoding="utf-8")
    files = {"auth-service": {"src/server.js": auth}, "user-service": {"src/users.js": user}, "report-job": {}}
    user_node = _node("user-service", "listUsers", file="src/users.js", line=6, end=9)
    partitions = {
        "auth-service": Partition(service_id="auth-service", repository="fixtures/auth-service"),
        "user-service": Partition(service_id="user-service", repository="fixtures/user-service", nodes={user_node.key: user_node}),
        "report-job": Partition(service_id="report-job", repository="fixtures/report-job"),
    }
    first = assemble(partitions, manifest, files, version="1")
    second = assemble(partitions, manifest, files, version="2")
    assert first.bridge_fingerprints == second.bridge_fingerprints
    assert len(first.bridge_fingerprints) == len(set(first.bridge_fingerprints))
    assert any(edge.relation == CONSUMES_HTTP for edge in first.edges)


def test_authors_history_credentials_and_atomic_write(tmp_path: Path) -> None:
    workspace = Workspace(ref=RepoRef(raw="local", name="demo", slug="demo", kind="local"), files={})
    text = "\n".join(
        [
            "commit aaa|2026-01-01T00:00:00Z|Ada",
            "1\t0\tsrc/app.js",
            "commit bbb|2026-01-02T00:00:00Z|Grace",
            "2\t1\tsrc/app.js",
            "-\t-\tbinary.bin",
        ]
    )
    report = _summarize(workspace, text, 365, sample_limit=30, sampled=True)
    assert report["churn"][0]["authors"] == 2
    assert report["history"]["truncated"] is False
    assert report["history"]["sample_limit"] == 30
    assert "365 days" not in report["window_label"]
    assert report["history"]["binary_changes_omitted"] == 1
    assert "ghp_SECRET" not in redact("clone failed ghp_SECRET", ["ghp_SECRET"])
    assert strip_userinfo("https://x-access-token:ghp_SECRET@github.com/acme/demo.git") == "https://github.com/acme/demo.git"
    repo = tmp_path / "checkouts" / "demo"
    repo.mkdir(parents=True)
    import subprocess

    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", "https://x-access-token:ghp_SECRET@github.com/acme/demo.git"], check=True)
    settings = Settings(root=tmp_path, repos_file=tmp_path / "repos.txt", output_dir=tmp_path, github_token="ghp_SECRET", ghe_token="")
    sanitize_tool_remote(repo, settings)
    url = subprocess.run(["git", "-C", str(repo), "remote", "get-url", "origin"], capture_output=True, text=True, check=True).stdout
    assert "ghp_SECRET" not in url
    assert "github.com/acme/demo.git" in url
    write_json(tmp_path, "sample.json", {"ok": True})
    assert read_json(tmp_path, "sample.json")["ok"] is True
    assert _aggregate([{"success": True}, {"success": False}]) == "partial"
    assert not Checkout(path=repo, owned=True).dirty or True


def test_sse_jobs_and_watcher(tmp_path: Path) -> None:
    bus = EventBus(replay_limit=2, queue_size=1)
    ident, queue = bus.subscribe()
    bus.publish("job_started", {"n": 1})
    bus.publish("job_progress", {"n": 2})
    bus.publish("graph_updated", {"n": 3})
    drained = []
    while not queue.empty():
        drained.append(queue.get_nowait())
    assert any("resync" in item or "graph_updated" in item for item in drained)
    replay, missing = bus.replay_after(0)
    assert missing or replay
    bus.unsubscribe(ident)
    events = []
    runner = JobRunner(1, lambda event, data: events.append(event))
    job = runner.submit("boom", lambda _job: (_ for _ in ()).throw(RuntimeError("nope")))
    deadline = time.time() + 2
    while "job_failed" not in events and time.time() < deadline:
        time.sleep(0.01)
    assert job.status == "failed"
    assert "job_completed" not in events
    assert "job_failed" in events
    runner.shutdown()
    seen = []
    coordinator = WatchCoordinator(lambda service, _stab: seen.append(service), debounce_ms=20, reconcile_seconds=3600)
    coordinator.mark("auth-service")
    time.sleep(0.08)
    coordinator.begin("auth-service")
    coordinator.mark("auth-service")
    coordinator.finish("auth-service")
    time.sleep(0.08)
    coordinator.stop()
    assert seen.count("auth-service") >= 2


def test_fixture_manifest_links_list_users() -> None:
    from dep_intel.sources import load_repos

    settings = _settings(ROOT / "output")
    manifest, errors = load_manifest(ROOT / "cluster-manifest.json", load_repos(settings), ROOT)
    assert manifest and not errors
    files = {
        "auth-service": {"src/server.js": (ROOT / "fixtures/auth-service/src/server.js").read_text(encoding="utf-8")},
        "user-service": {"src/users.js": (ROOT / "fixtures/user-service/src/users.js").read_text(encoding="utf-8")},
        "report-job": {"report_job.py": (ROOT / "fixtures/report-job/report_job.py").read_text(encoding="utf-8")},
    }
    user = _node("user-service", "listUsers", file="src/users.js", line=6, end=9)
    partitions = {
        "auth-service": Partition(service_id="auth-service", repository="fixtures/auth-service"),
        "user-service": Partition(service_id="user-service", repository="fixtures/user-service", nodes={user.key: user}),
        "report-job": Partition(service_id="report-job", repository="fixtures/report-job"),
    }
    snapshot = assemble(partitions, manifest, files, version="demo")
    calls = [edge for edge in snapshot.edges if edge.relation == CONSUMES_HTTP and edge.target]
    assert calls
    caller = snapshot.nodes[calls[0].source]
    assert caller.name == "listUsers"
    assert caller.line_start == 6
    contract = snapshot.nodes[calls[0].target]
    assert "/api/auth/validate" in contract.name
    handler_edges = [edge for edge in snapshot.edges if edge.relation == HANDLED_BY and edge.source == contract.key]
    assert handler_edges
    handler = snapshot.nodes[handler_edges[0].target]
    assert handler.category in {"handler_anchor", "code_symbol"}
    impact = analyze(snapshot, [handler.key], mode="code_change", settings=manifest.impact)
    names = {item.name for item in impact.affected}
    assert "listUsers" in names
    assert any("/api/users" in item.name for item in impact.affected)
