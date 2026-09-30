---
name: dep-intel-scan
description: Runs the dep-intel cross-repo scan (languages, dependencies, updates, APIs, review, coverage, advisories, import graph, commit history). Use when the user asks to scan dependencies, review repos, run dep-intel, or refresh the dashboard.
---

# Dependency scan

The tools live on the `dep-intel` MCP server. Call them in this order. Each one writes a JSON file under `output/`.

1. `detect_language`
2. `analyze_dependencies`
3. `check_package_updates`
4. `detect_api_dependencies`
5. `review_code`
6. `run_coverage`
7. `scan_vulnerabilities`
8. `scan_cve_from_github_issues`
9. `generate_security_release_report`
10. `build_repo_graph`
11. `analyze_commit_history`

Or call `run_dependency_scan` once. It runs that same sequence.

`repos.txt` lists local paths or GitHub URLs. GitHub Enterprise hosts need `GHE_TOKEN`. github.com private repos need `GITHUB_TOKEN`. Do not invent repository URLs and do not overwrite `repos.txt` unless the user asked.

After the run, summarize repository count, package count, update count, and finding count. Mention `meta.generated_at` from the output files. The Carbon dashboard at http://127.0.0.1:3002 reads those files over SSE.

Do not commit `.env` or tokens.
