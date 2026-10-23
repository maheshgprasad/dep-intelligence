# Cursor, Claude, and IBM Bob

One stdio MCP server, `scripts/dep-intel-mcp.sh`, registers the analysis tools. Install the Python virtualenv first (`Docs/SETUP_GUIDE.md`). The script runs `server/.venv/bin/python -m dep_intel.mcp_server`. **Nami Rank Module (NRM)** is exposed as `analyze_hybrid_impact`. It returns ranked impact candidates and an evidence bundle for Cursor and IBM Bob. **Nami Boundary Gate** uses the same server: `build_change_view`, `review_change`, `get_review`, `explain_review_finding`, and `record_review_feedback`. Those tools call the same review engine as the REST API. Feedback does not change NRM scores.

Skills are written once under `skills/` and linked into each client. `install-mcp-pydriller` is the install step: it installs the MCP server and PyDriller.

| Client | MCP config | Skills |
| --- | --- | --- |
| Cursor | `.cursor/mcp.json` | `.cursor/skills/` |
| Claude Code | `.mcp.json` | `.claude/skills/` |
| IBM Bob | `.bob/config.json` | `.bob/skills/` |

## Cursor

Open this repository. Cursor reads `.cursor/mcp.json`. Enable the `dep-intel` server if it asks. Skills load from `.cursor/skills/`.

## Claude Code

`.mcp.json` points at the same script. Approve the project MCP server when Claude Code prompts. Skills load from `.claude/skills/`. `CLAUDE.md` points at `AGENTS.md`.

## IBM Bob

Merge `.bob/config.json` into `~/.bob/mcp_servers.json`. Set `PROJECT_ROOT` to this repository. Bob expands `${PROJECT_ROOT}` and the token placeholders from the environment. Restart Bob and confirm `dep-intel` appears in `bob tools list`.

Skills in `.bob/skills/` use the same `SKILL.md` files as Cursor and Claude.

## Shared rules

`AGENTS.md` is the governance file for all three. A scan may read `repos.txt` and write `output/`. It does not commit tokens or edit repositories that are not listed.
