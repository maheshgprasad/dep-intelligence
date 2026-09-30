"""Code graph built by code-review-graph.

Each repository is checked out, then code-review-graph parses it and writes
graph.db under output/graphs/<slug>/. The dashboard snapshot is a projection
of that database: nodes, communities, hubs, bridges, and quality findings.
"""

from __future__ import annotations

from pathlib import Path

from dep_intel.config import Settings
from dep_intel.sources import Workspace, ensure_checkout
from dep_intel.store import now, read_json, write_json


def build_graphs(workspaces: list[Workspace], settings: Settings) -> dict:
    manifest = []
    for workspace in workspaces:
        slug_dir = settings.output_dir / "graphs" / workspace.ref.slug
        slug_dir.mkdir(parents=True, exist_ok=True)
        try:
            checkout = ensure_checkout(workspace.ref, settings)
            graph = _from_crg(checkout, slug_dir, workspace)
        except Exception as exc:
            graph = _empty(workspace, str(exc))
        write_json(slug_dir, "graph.json", graph)
        manifest.append(
            {
                "slug": workspace.ref.slug,
                "name": workspace.ref.name,
                "url": workspace.ref.raw,
                "checkout": graph.get("checkout", ""),
                "builtAt": now(),
                "stats": {
                    "files": graph["stats"]["files"],
                    "edges": graph["stats"]["edges"],
                    "communities": len(graph["communities"]),
                },
                "error": graph.get("error", ""),
            }
        )
    payload = {"meta": {"generated_at": now(), "source": "code-review-graph"}, "repos": manifest}
    write_json(settings.output_dir, "repo_graphs.json", payload)
    built = sum(1 for item in manifest if not item["error"])
    return {"success": built == len(manifest), "message": f"Built code-review-graph databases for {built} of {len(manifest)} repositories."}


def impact_for(settings: Settings, slug: str, file: str) -> dict:
    """Blast radius from code-review-graph for one file in a built repository."""
    manifest = read_json(settings.output_dir, "repo_graphs.json") or {}
    entry = next((item for item in manifest.get("repos") or [] if item.get("slug") == slug), None)
    if not entry or not entry.get("checkout"):
        return {"summary": "Build the code graph before asking for impact.", "nodes": []}
    if not file.strip():
        return {"summary": "", "nodes": []}
    from code_review_graph.tools.query import get_impact_radius

    result = get_impact_radius(
        changed_files=[file.strip()],
        repo_root=entry["checkout"],
        max_depth=2,
        max_results=40,
    )
    root = Path(entry["checkout"])
    nodes = []
    seen = set()
    for path in result.get("impacted_files") or []:
        relative = _relative(root, str(path))
        if relative in seen:
            continue
        seen.add(relative)
        nodes.append({"id": relative, "language": Path(relative).suffix.lstrip("."), "lines": 0})
    summary = str(result.get("summary") or result.get("error") or "")
    return {"summary": summary, "nodes": nodes}


def _from_crg(checkout: Path, slug_dir: Path, workspace: Workspace) -> dict:
    from code_review_graph.analysis import find_bridge_nodes, find_hub_nodes
    from code_review_graph.communities import get_communities
    from code_review_graph.graph import GraphStore
    from code_review_graph.incremental import get_db_path
    from code_review_graph.refactor import find_dead_code
    from code_review_graph.registry import Registry
    from code_review_graph.tools.build import build_or_update_graph
    from code_review_graph.tools.query import find_large_functions

    if not (checkout / ".git").exists() and not (checkout / ".svn").exists():
        raise RuntimeError(f"{checkout} has no git history, so code-review-graph cannot build it.")
    Registry().set_data_dir(str(checkout), str(slug_dir))
    build_or_update_graph(full_rebuild=True, repo_root=str(checkout), postprocess="full")
    store = GraphStore(get_db_path(checkout))
    try:
        file_nodes = store.get_all_nodes(exclude_files=False)
        files = [node for node in file_nodes if node.kind == "File"]
        symbols = [node for node in file_nodes if node.kind != "File"]
        edges = []
        seen_edges = set()
        for edge in store.get_all_edges():
            left = _relative(checkout, edge.file_path or "")
            target = ""
            # Prefer the target node's file when the edge stores a qualified name.
            right = left
            for node in symbols:
                if node.qualified_name == edge.target_qualified and node.file_path:
                    target = _relative(checkout, node.file_path)
                    break
            if target:
                right = target
            if not left or not right or left == right:
                continue
            key = (edge.kind, *sorted((left, right)))
            if key in seen_edges:
                continue
            seen_edges.add(key)
            edges.append({"from": left, "to": right, "kind": edge.kind})
        communities = []
        names = {}
        for community in get_communities(store, sort_by="size"):
            names[community["id"]] = community["name"]
            members = community.get("members") or []
            communities.append(
                {
                    "id": community["name"] or str(community["id"]),
                    "size": community["size"],
                    "files": members[:20],
                }
            )
        hubs = [
            {"id": f"{hub['name']} ({_relative(checkout, hub.get('file') or '')})", "degree": hub["total_degree"]}
            for hub in find_hub_nodes(store, top_n=12)
        ]
        bridges = [
            {
                "id": f"{bridge['name']} ({_relative(checkout, bridge.get('file') or '')})",
                "connects": [names.get(bridge.get("community_id"), str(bridge.get("community_id") or ""))],
            }
            for bridge in find_bridge_nodes(store, top_n=12)
        ]
        search_nodes = []
        for node in (files + symbols)[:400]:
            relative = _relative(checkout, node.file_path or "")
            lines = 0
            if node.line_start and node.line_end and node.line_end >= node.line_start:
                lines = node.line_end - node.line_start + 1
            label = relative if node.kind == "File" else f"{node.name} ({relative})"
            search_nodes.append(
                {"id": label, "language": node.language or Path(relative).suffix.lstrip("."), "lines": lines}
            )
    finally:
        store.close()
    large = find_large_functions(min_lines=80, kind="File", limit=20, repo_root=str(checkout))
    dead_store = GraphStore(get_db_path(checkout))
    try:
        dead = find_dead_code(store=dead_store, root=checkout)
        large_files = []
        for item in (large.get("results") or large.get("functions") or [])[:20]:
            path = item.get("relative_path") or item.get("file_path") or item.get("file") or item.get("name")
            large_files.append({"id": _relative(checkout, str(path)), "lines": item.get("line_count") or item.get("lines") or 0})
        isolated = []
        for item in dead[:20]:
            path = item.get("relative_path") or item.get("file_path") or ""
            isolated.append(f"{item.get('kind', 'symbol')} {item.get('name')} ({_relative(checkout, path)})")
    finally:
        dead_store.close()
    return {
        "slug": workspace.ref.slug,
        "checkout": str(checkout),
        "source": "code-review-graph",
        "nodes": search_nodes,
        "edges": edges[:2000],
        "communities": communities,
        "hubs": hubs,
        "bridges": bridges,
        "quality": {"large_files": large_files, "isolated_files": isolated},
        "stats": {"files": len(files), "edges": len(edges)},
    }


def _empty(workspace: Workspace, message: str) -> dict:
    return {
        "slug": workspace.ref.slug,
        "checkout": "",
        "source": "code-review-graph",
        "nodes": [],
        "edges": [],
        "communities": [],
        "hubs": [],
        "bridges": [],
        "quality": {"large_files": [], "isolated_files": []},
        "stats": {"files": 0, "edges": 0},
        "error": message,
    }


def _relative(root: Path, file_path: str) -> str:
    if not file_path:
        return ""
    path = Path(file_path)
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except (OSError, ValueError):
        return path.as_posix()
