---
name: setup-code-graph
description: Builds the dep-intel import graph and commit hotspots for the Carbon code graph page. Use when the user asks to set up the code graph, build graphs, or enable commit hotspots.
---

# Code graph

This repository builds a structural import graph and a git co-change report. It does not shell out to code-review-graph.

1. Confirm `repos.txt` has at least one local path or GitHub URL.
2. Confirm `server/.venv` exists. If it does not, tell the user to run the setup in `Docs/SETUP_GUIDE.md`.
3. Call the MCP tool `build_repo_graph`, then `analyze_commit_history`.
4. Open http://127.0.0.1:3002/graph and check Search, Communities, Hubs and bridges, Impact, Quality, and Hotspots.

Local paths need a `.git` directory before Hotspots has commits. GitHub URLs use the commits API and need a token when the repo is private.

Hidden coupling is a co-change pair with `is_structural: false`: the files change together and the import graph does not connect them.
