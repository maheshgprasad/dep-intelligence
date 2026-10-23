"""Contract changes reuse the existing extractors."""

from __future__ import annotations

from pathlib import Path

from dep_intel.change_view import build_change_view
from dep_intel.config import PROJECT_ROOT
from dep_intel.cross_repo_graph import index_snapshot
from dep_intel.graph_models import CONSUMES_HTTP, HTTP_CONTRACT, GraphEdge, GraphNode, Partition, symbol_key

FIXTURE = (PROJECT_ROOT / "fixtures/auth-service/src/server.js").read_text(encoding="utf-8")


def test_removed_route_names_the_known_consumer() -> None:
    new = FIXTURE.replace('app.post("/api/auth/validate", (req, res) => {\n  res.json({ ok: true });\n});\n\n', "")
    view = _view(FIXTURE, new)
    removed = [item for item in view["contract_changes"] if item["kind"] == "operation_removed"]
    assert removed
    assert removed[0]["breaking"] is True
    assert removed[0]["consumers"][0]["service_id"] == "user-service"


def test_body_edit_is_not_an_api_break() -> None:
    new = FIXTURE.replace("{ ok: true }", "{ ok: true, edited: true }")
    view = _view(FIXTURE, new)
    assert not any(item["breaking"] for item in view["contract_changes"])


def test_added_route_is_not_breaking() -> None:
    new = FIXTURE.replace("app.listen(3001);", 'app.get("/api/auth/health", (req, res) => res.json({ ok: true }));\n\napp.listen(3001);')
    view = _view(FIXTURE, new)
    added = [item for item in view["contract_changes"] if item["kind"] == "operation_added"]
    assert added
    assert added[0]["breaking"] is False


def test_proto_change_does_not_invent_discovery() -> None:
    view = _view("service Auth {}", "service Auth { rpc Ping(Empty) returns (Empty); }", path="api.proto")
    assert view["contract_changes"] == []


def _view(old: str, new: str, path: str = "src/server.js") -> dict:
    resolved = {
        "kind": "explicit",
        "explicit_files": [{"path": path, "old_text": old, "new_text": new, "change_type": "modified"}],
        "diagnostics": [],
        "base_sha": "base",
        "head_sha": "head",
    }
    return build_change_view(resolved, service_id="auth-service", repository="fixtures/auth-service", snapshot=_snapshot(), base_snapshot=None, repo_root=Path("."))


def _snapshot():
    identity = "http::POST:/api/auth/validate"
    contract = GraphNode(
        key=symbol_key("auth-service", "", identity, HTTP_CONTRACT),
        category=HTTP_CONTRACT,
        service_id="auth-service",
        name="POST /api/auth/validate",
        qualified_name=identity,
        file_path="",
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
    auth = Partition(service_id="auth-service", repository="fixtures/auth-service")
    user = Partition(service_id="user-service", repository="fixtures/user-service")
    auth.nodes[contract.key] = contract
    user.nodes[caller.key] = caller
    edge = GraphEdge(caller.key, contract.key, CONSUMES_HTTP, {"status": "extracted"})
    return index_snapshot(version="g", generated_at="", partitions={"auth-service": auth, "user-service": user}, overlay={}, bridges=[edge], diagnostics=[], fingerprints=[])
