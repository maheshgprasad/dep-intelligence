"""Import graph for the code graph page.

This is a structural graph built from import and require edges. It is not the
code-review-graph SQLite database. The page says so.
"""

from __future__ import annotations

import re
from pathlib import Path

from dep_intel.config import Settings
from dep_intel.sources import Workspace
from dep_intel.store import now, write_json

_JS_IMPORT = re.compile(r"(?:require\(\s*|from\s+)['\"](\.[^'\"]+)['\"]")
_PY_IMPORT = re.compile(r"^(?:from|import)\s+([A-Za-z_][\w.]*)", re.M)


def build_graphs(workspaces: list[Workspace], settings: Settings) -> dict:
    manifest = []
    for workspace in workspaces:
        graph = _graph(workspace)
        slug_dir = settings.output_dir / "graphs" / workspace.ref.slug
        slug_dir.mkdir(parents=True, exist_ok=True)
        write_json(slug_dir, "graph.json", graph)
        manifest.append(
            {
                "slug": workspace.ref.slug,
                "name": workspace.ref.name,
                "url": workspace.ref.raw,
                "builtAt": now(),
                "stats": {
                    "files": len(graph["nodes"]),
                    "edges": len(graph["edges"]),
                    "communities": len(graph["communities"]),
                },
            }
        )
    payload = {"meta": {"generated_at": now(), "source": "dep-intel"}, "repos": manifest}
    write_json(settings.output_dir, "repo_graphs.json", payload)
    return {"success": True, "message": f"Built import graphs for {len(manifest)} repositories."}


def _graph(workspace: Workspace) -> dict:
    nodes = []
    edges = []
    files = set(workspace.files)
    for path, text in workspace.files.items():
        if Path(path).suffix.lower() not in {".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".go"}:
            continue
        line_count = text.count("\n") + 1
        nodes.append({"id": path, "language": Path(path).suffix.lstrip("."), "lines": line_count})
        for target in _targets(path, text, files):
            edges.append({"from": path, "to": target, "kind": "import"})
    communities: dict[str, list[str]] = {}
    for node in nodes:
        group = node["id"].split("/", 1)[0] if "/" in node["id"] else "(root)"
        communities.setdefault(group, []).append(node["id"])
    degree: dict[str, int] = {node["id"]: 0 for node in nodes}
    for edge in edges:
        degree[edge["from"]] = degree.get(edge["from"], 0) + 1
        degree[edge["to"]] = degree.get(edge["to"], 0) + 1
    hubs = [
        {"id": node_id, "degree": score}
        for node_id, score in sorted(degree.items(), key=lambda item: item[1], reverse=True)
        if score
    ][:12]
    community_of = {file: name for name, members in communities.items() for file in members}
    bridges = []
    for node in nodes:
        groups = {community_of.get(edge["to"]) for edge in edges if edge["from"] == node["id"]}
        groups.discard(None)
        groups.discard(community_of.get(node["id"]))
        if groups:
            bridges.append({"id": node["id"], "connects": sorted(groups)})
    large = [node for node in nodes if node["lines"] >= 80]
    connected = {edge["from"] for edge in edges} | {edge["to"] for edge in edges}
    isolated = [node["id"] for node in nodes if node["id"] not in connected and "test" not in node["id"]]
    return {
        "slug": workspace.ref.slug,
        "nodes": nodes,
        "edges": edges,
        "communities": [{"id": name, "files": members, "size": len(members)} for name, members in communities.items()],
        "hubs": hubs,
        "bridges": bridges,
        "quality": {"large_files": large, "isolated_files": isolated},
        "note": "Structural import graph. Execution flows from code-review-graph are not computed here.",
    }


def _targets(path: str, text: str, files: set[str]) -> list[str]:
    found = []
    parent = str(Path(path).parent)
    if parent == ".":
        parent = ""
    for match in _JS_IMPORT.findall(text):
        resolved = _resolve(parent, match, files)
        if resolved:
            found.append(resolved)
    if path.endswith(".py"):
        for module in _PY_IMPORT.findall(text):
            candidate = module.split(".")[0] + ".py"
            if candidate in files:
                found.append(candidate)
    return found


def _resolve(parent: str, spec: str, files: set[str]) -> str:
    raw = spec[2:] if spec.startswith("./") else spec
    base = f"{parent}/{raw}" if parent else raw
    base = str(Path(base))
    for candidate in (base, f"{base}.js", f"{base}.ts", f"{base}.tsx", f"{base}/index.js"):
        normalized = candidate.replace("\\", "/")
        if normalized in files:
            return normalized
    return ""
