# Agents

dep-intel is a React + Carbon dashboard and a Python analysis service. Cursor, Claude, and IBM Bob share one MCP server: `scripts/dep-intel-mcp.sh`.

## Approved without another confirmation

- Read `repos.txt`
- Call the dep-intel MCP tools
- Write under `output/`

## Ask first

- Changing files outside `output/`, `Docs/`, and the dashboard when the user did not ask for that
- Opening pull requests
- Replacing `repos.txt`

## Do not

- Commit tokens or `.env`
- Merge pull requests
- Scan repositories that are not listed in `repos.txt`
- Put new documentation outside `Docs/`, except `README.md`, `AGENTS.md`, and `CLAUDE.md`

## Evidence

A scan writes JSON under `output/` with `meta.generated_at`. The reply should include repository count, package count, and finding count.

Run `detect_language` before `run_coverage`. Run `analyze_dependencies` before you treat update results as current.
