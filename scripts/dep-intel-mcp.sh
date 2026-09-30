#!/bin/sh
ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
PYTHON="$ROOT/server/.venv/bin/python"
if [ ! -x "$PYTHON" ]; then
  echo "dep-intel: create the virtualenv first: python3 -m venv server/.venv && server/.venv/bin/pip install -e 'server[dev]'" >&2
  exit 1
fi
cd "$ROOT/server"
exec "$PYTHON" -m dep_intel.mcp_server
