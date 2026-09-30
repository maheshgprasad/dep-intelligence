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

`repos.txt` already points at three local fixtures, so **Run analysis** works without a GitHub token. Add GitHub URLs to that file when you want remote repositories. Put `GITHUB_TOKEN` or `GHE_TOKEN` in `.env` (see `.env.example`).

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
| `analyze_commit_history` | `output/graphs/<slug>/cochange.json` |

Code graph pages show the code-review-graph MCP payloads. Nami Trace stores those payloads and does not recompute communities, hubs, flows, or impact.

Client setup: [Docs/AGENT_CLIENTS.md](Docs/AGENT_CLIENTS.md).
