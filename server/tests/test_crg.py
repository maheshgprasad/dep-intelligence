import pytest

from dep_intel.crg import prepare_call


def test_query_injects_repo_root_and_ignores_client_path() -> None:
    args = prepare_call(
        "semantic_search_nodes_tool",
        "/repos/demo",
        {"query": "chat", "repo_root": "/tmp/other"},
    )
    assert args["repo_root"] == "/repos/demo"
    assert args["query"] == "chat"


def test_dashboard_cannot_apply_refactors() -> None:
    with pytest.raises(ValueError):
        prepare_call("apply_refactor_tool", "/repos/demo", {"refactor_id": "abc"})


def test_refactor_query_is_read_only() -> None:
    args = prepare_call("refactor_tool", "/repos/demo", {"mode": "dead_code"})
    assert args["mode"] == "dead_code"
    with pytest.raises(ValueError):
        prepare_call("refactor_tool", "/repos/demo", {"mode": "rename", "old_name": "a", "new_name": "b"})
