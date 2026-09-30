#!/bin/sh
ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
PYTHON="$ROOT/server/.venv/bin/python"
if [ ! -x "$PYTHON" ]; then
  echo "dep-intel: run skills/install-mcp-pydriller/scripts/install.sh first" >&2
  exit 1
fi
cd "$ROOT/server"
exec "$PYTHON" -m dep_intel.mcp_server
