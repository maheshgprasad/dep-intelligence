---
name: setup-code-graph
description: Builds Nami Trace code graphs through the code-review-graph MCP server. Use when the user asks to set up the code graph, build graphs, or enable commit hotspots.
---

# Code graph

Graph communities, hubs, bridges, flows, impact, and quality come from the code-review-graph MCP server. Nami Trace stores those tool payloads. It does not recompute them.

1. Confirm `repos.txt` has at least one local path or GitHub URL.
2. Confirm `server/.venv` exists. If it does not, tell the user to run the setup in `Docs/SETUP_GUIDE.md`.
3. Start the dashboard with `scripts/nami-trace.sh` if it is not already running.
4. Call the MCP tool `build_repo_graph`, then `analyze_commit_history`.
5. Open http://127.0.0.1:3002/graph and check Overview, Search, Flows, Communities, Hubs and bridges, Impact, Architecture, Quality, Refactor, Coupling, Changes, and Hotspots.

Local paths need a `.git` directory. GitHub URLs are cloned under `output/checkouts/`. Hotspots are commit history. Coupling on the Coupling tab is the code-review-graph surprise score.
