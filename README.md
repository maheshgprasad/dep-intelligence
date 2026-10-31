# Nami Trace

Cross-repository dependency intelligence. The dashboard is a React app on [Carbon](https://carbondesignsystem.com/). Analysis is the same Python process. Cursor, Claude, and IBM Bob call that service over MCP.

The behavior is taken from the notes in [Docs/spec](Docs/spec). This tree is a new implementation of that spec, not the earlier Express app.

## Run

```bash
skills/install-mcp-pydriller/scripts/install.sh
cd web && npm install && cd ..
```

The install script creates `server/.venv` and installs the MCP server and PyDriller.

One process serves the dashboard and the API:

```bash
./scripts/nami-trace.sh
```

Dashboard: http://127.0.0.1:3002

Startup loads the last files under `output/`. It does not rebuild graphs or watch `graph.db`. The dashboard reads a small application state from `GET /api/app-state`. `GET /api/snapshot` remains for compatibility and still returns the full analysis payload.

The normal flow is **Refresh intelligence**, **Review change**, **Explore impact**, **Architecture**, **Repository health**, and **Research**. Refresh intelligence rebuilds repository graphs, commit history, temporal evidence, the cluster graph, and contracts. **Run repository health scan** is separate: packages, updates, OSV advisories, coverage, and optional vendor checks. It does not rebuild the graph or run Boundary Gate.

Nami Rank Module ranks downstream files. A rank is not a probability and it does not block a change by itself. Nami Boundary Gate reviews an actual change and can pass, warn, block, or return incomplete. High rank alone does not block, and temporal-only evidence alone does not block.

`NAMI_EAGER_GRAPH_REFRESH=1` and `NAMI_CLUSTER_WATCH=1` restore the boot-time graph jobs. Add repositories to `repos.txt`. Put `GITHUB_TOKEN` or `GHE_TOKEN` in `.env` (see `.env.example`).

## What the scan writes

| Tool | File |
| --- | --- |
| `detect_language` | `output/language_detection.json` |
| `analyze_dependencies` | `output/dep_matrix.json` |
| `check_package_updates` | `output/package_updates.json` |
| `detect_api_dependencies` | `output/api_dependencies.json` |
| `review_code` | `output/code_review.json` |
| `run_coverage` | `output/test_coverage.json` |
| `scan_vulnerabilities` | `output/vulnerabilities.json` |
| `scan_cve_from_github_issues` | `output/cve_analysis.json` |
| `generate_security_release_report` | `output/security_release_report.json` |
| `build_repo_graph` | `output/repo_graphs.json` and `output/graphs/<slug>/crg.json` |
| `analyze_commit_history` | `output/graphs/<slug>/cochange.json` and `output/graphs/<slug>/temporal_evidence.json` |

Code graph pages show the code-review-graph MCP payloads. Nami Trace stores those payloads and does not recompute communities, hubs, or flows.

Structural change impact is a separate cluster graph. It reads each repository's CRG database, resolves manifest-declared HTTP, gRPC, and event contracts, and scores dependents. The score is not a probability of breakage. See [Docs/CROSS_REPO_IMPACT.md](Docs/CROSS_REPO_IMPACT.md). **Nami Rank Module v2** ranks files rather than individual graph symbols. Structural paths remain symbol-level evidence under each file. NRM can use commit history or a precomputed Phase-1 artifact. Historical runs expose causal-validity metadata. See [Docs/NAMI_RANK_MODULE.md](Docs/NAMI_RANK_MODULE.md) and [Docs/NRM_EVALUATION.md](Docs/NRM_EVALUATION.md). Those ranking scores are not probabilities. PyDriller is installed and is not used to train a model.

**Nami Boundary Gate** reviews one change. It builds a Change View, asks Nami Rank for downstream files, applies `nami-review.yaml`, and returns pass, warn, block, or incomplete. Ranking scores stay ranking evidence. Gate feedback does not change NRM. Boundary Gate decisions can be independently replayed and validated against explicit evidence requirements. Optional LLM commentary translates validated results into concise operational language but cannot alter Gate decisions. The stored review explains the decision with evidence, inferences, and actions, and can be exported as Markdown, JSON, PDF, or an evidence package. See [Docs/BOUNDARY_GATE.md](Docs/BOUNDARY_GATE.md), [Docs/GATE_VALIDATION.md](Docs/GATE_VALIDATION.md), [Docs/REVIEW_EVIDENCE.md](Docs/REVIEW_EVIDENCE.md), and [Docs/EXPORTS.md](Docs/EXPORTS.md).

Repository and cluster intelligence reports are a separate scope. They describe the published snapshot: services, contracts, relationships, history, health, and gaps. They do not return pass, warn, or block. See [Docs/REPORTING.md](Docs/REPORTING.md).

```bash
server/.venv/bin/python scripts/review-change.py --repository fixtures/auth-service --working-tree
server/.venv/bin/python scripts/demo-boundary-gate.py
```

Client setup: [Docs/AGENT_CLIENTS.md](Docs/AGENT_CLIENTS.md).
