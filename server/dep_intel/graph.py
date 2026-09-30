"""Persist code-review-graph MCP results for the dashboard.

The dashboard stores each tool payload as returned. It does not derive
communities, hubs, bridges, or blast radius itself.
"""

from __future__ import annotations

import threading
from typing import Any

from dep_intel.config import Settings
from dep_intel.crg import call_tool
from dep_intel.sources import Workspace, ensure_checkout
from dep_intel.store import now, read_json, write_json

_job = threading.Lock()

# Tool name -> arguments. repo_root is added by the MCP client.
COLLECT: list[tuple[str, str, dict[str, Any]]] = [
    ("stats", "list_graph_stats_tool", {}),
    ("minimal", "get_minimal_context_tool", {"task": "code graph"}),
    ("communities", "list_communities_tool", {"sort_by": "size", "detail_level": "standard", "max_results": 50, "max_members": 20}),
    ("hubs", "get_hub_nodes_tool", {"top_n": 15, "detail_level": "standard"}),
    ("bridges", "get_bridge_nodes_tool", {"top_n": 15, "detail_level": "standard"}),
    ("flows", "list_flows_tool", {"sort_by": "criticality", "limit": 40, "detail_level": "standard"}),
    ("architecture", "get_architecture_overview_tool", {"detail_level": "minimal", "max_results": 40}),
    ("large_functions", "find_large_functions_tool", {"min_lines": 50, "limit": 40}),
    ("knowledge_gaps", "get_knowledge_gaps_tool", {"max_per_category": 15, "detail_level": "standard"}),
    ("surprises", "get_surprising_connections_tool", {"top_n": 15, "detail_level": "standard"}),
    ("questions", "get_suggested_questions_tool", {}),
    ("dead_code", "refactor_tool", {"mode": "dead_code", "max_results": 40, "detail_level": "standard"}),
    ("refactor", "refactor_tool", {"mode": "suggest", "max_results": 40, "detail_level": "standard"}),
    ("changes", "detect_changes_tool", {"detail_level": "minimal", "include_source": False, "max_results": 20, "max_flows": 10}),
]


def build_graphs(workspaces: list[Workspace], settings: Settings) -> dict:
    with _job:
        return _write_manifest(workspaces, settings, rebuild=True)


def refresh_graphs(settings: Settings) -> dict:
    """Read the current MCP results for checkouts that already have a graph."""
    from dep_intel.sources import load_repos

    refs = load_repos(settings)
    workspaces = [Workspace(ref=ref) for ref in refs]
    with _job:
        return _write_manifest(workspaces, settings, rebuild=False)


def checkout_for(settings: Settings, slug: str) -> str:
    manifest = read_json(settings.output_dir, "repo_graphs.json") or {}
    for item in manifest.get("repos") or []:
        if item.get("slug") == slug and item.get("checkout"):
            return str(item["checkout"])
    from dep_intel.sources import load_repos

    for ref in load_repos(settings):
        if ref.slug == slug:
            return str(ensure_checkout(ref, settings))
    raise KeyError(slug)


def _write_manifest(workspaces: list[Workspace], settings: Settings, *, rebuild: bool) -> dict:
    manifest = []
    for workspace in workspaces:
        slug_dir = settings.output_dir / "graphs" / workspace.ref.slug
        slug_dir.mkdir(parents=True, exist_ok=True)
        record, payload = _one(workspace, settings, rebuild=rebuild)
        if payload is not None:
            write_json(slug_dir, "crg.json", payload)
        manifest.append(record)
    envelope = {"meta": {"generated_at": now(), "source": "code-review-graph"}, "repos": manifest}
    write_json(settings.output_dir, "repo_graphs.json", envelope)
    built = sum(1 for item in manifest if not item.get("error"))
    action = "Updated" if rebuild else "Read"
    return {
        "success": built == len(manifest) and built > 0,
        "message": f"{action} code-review-graph results for {built} of {len(manifest)} repositories.",
    }


def _one(workspace: Workspace, settings: Settings, *, rebuild: bool) -> tuple[dict[str, Any], dict[str, Any] | None]:
    base = {
        "slug": workspace.ref.slug,
        "name": workspace.ref.name,
        "url": workspace.ref.raw,
        "checkout": "",
        "builtAt": now(),
        "stats": {},
        "error": "",
    }
    try:
        checkout = ensure_checkout(workspace.ref, settings)
    except Exception as exc:
        base["error"] = str(exc)
        return base, None
    base["checkout"] = str(checkout)
    root = str(checkout)
    try:
        if rebuild:
            stats = call_tool("list_graph_stats_tool", root, {}, allow_build=True)
            full = not stats.get("total_nodes")
            call_tool(
                "build_or_update_graph_tool",
                root,
                {"full_rebuild": full, "postprocess": "full"},
                allow_build=True,
            )
        tools: dict[str, Any] = {}
        for key, tool, arguments in COLLECT:
            try:
                tools[key] = call_tool(tool, root, arguments, allow_build=True)
            except Exception as exc:
                tools[key] = {"summary": str(exc), "error": str(exc)}
        stats = tools.get("stats") or {}
        if not rebuild and not stats.get("total_nodes"):
            base["error"] = "Code graph is not built yet."
            base["stats"] = stats
            return base, None
        base["stats"] = stats
        payload = {
            "meta": {"generated_at": now(), "source": "code-review-graph", "repo_root": root},
            "tools": tools,
        }
        return base, payload
    except Exception as exc:
        base["error"] = str(exc)
        return base, None
