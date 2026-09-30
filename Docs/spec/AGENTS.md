# AGENTS.md — Bob Governance Rules for dep-intel-ui-v1

## Project Intent
Cross-repo dependency intelligence and code review dashboard for ZaaS zcrypto services.
Bob uses three MCP tools to populate the dashboard with live data.

## Agentic SDLC Loop (from Bob docs)
Orient → Bound → Plan → Act → Verify → Explain

## MCP Tools Required
| Tool | What it does | Output file |
|------|-------------|-------------|
| `analyze_dependencies` | Reads repos.txt, fetches manifests, builds dependency matrix | `output/dep_matrix.json` |
| `check_package_updates` | Reads dep_matrix.json, checks latest versions, classifies updates | `output/package_updates.json` |
| `review_code` | Fetches source files, scans for dead code + undefined variables | `output/code_review.json` |

## Approved Actions (no further approval needed)
- Read `repos.txt`
- Call `analyze_dependencies`, `check_package_updates`, `review_code` tools
- Write to `output/` directory

## Actions Requiring Human Approval
- Creating GitHub PRs (`prService.js`)
- Modifying any source file outside `output/`
- Creating Jira tickets (always dry-run first)

## What Bob Must NOT Do
- Commit credentials or tokens
- Merge pull requests automatically
- Delete or overwrite `repos.txt` without confirmation
- Access repos not listed in `repos.txt`

## Evidence Contract
Every Bob run must produce:
1. At least one JSON file in `output/`
2. A summary in Bob's response (repo count, package count, finding count)
3. The `meta.generated_at` timestamp in each output file

## Constraints
- Read-only by default (mode: read_only in .bob/config.json)
- Credentials from env vars only — never hardcoded
- Run `analyze_dependencies` before `check_package_updates`
- GHE_TOKEN required for live IBM GHE data; mock data used if absent
