---
name: install-mcp-pydriller
description: Installs the dep-intel MCP server and PyDriller into the project virtualenv. Use when installing the project, creating the virtualenv, setting up Cursor, Claude, or IBM Bob, or when the MCP server or PyDriller is missing.
---

# Install MCP server and PyDriller

Every install of this repository runs `skills/install-mcp-pydriller/scripts/install.sh` from the repository root. That script creates `server/.venv` when it is missing and installs the Python package. The package depends on `mcp` and `pydriller`, so the same install registers the `dep-intel-mcp` command and PyDriller.

Execute the script. Do not replace it with a hand-written `pip install`.

```bash
skills/install-mcp-pydriller/scripts/install.sh
```

The script is finished only when it prints `mcp` and `pydriller` versions and exits 0. After that, the MCP clients start the server with `scripts/dep-intel-mcp.sh`. Configurations are `.cursor/mcp.json`, `.mcp.json`, and `.bob/config.json`.
