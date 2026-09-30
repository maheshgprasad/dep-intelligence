#!/bin/sh
# Start Nami Trace: one process serves the dashboard and the analysis API.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
cd "$ROOT"
PYTHON="$ROOT/server/.venv/bin/python"
if [ ! -x "$PYTHON" ]; then
  echo "Nami Trace: create the virtualenv first: python3 -m venv server/.venv && server/.venv/bin/pip install -e 'server[dev]'" >&2
  exit 1
fi
if [ ! -d "$ROOT/web/node_modules" ]; then
  npm install --prefix "$ROOT/web"
fi
npm run build --prefix "$ROOT/web"
export PORT="${PORT:-3002}"
exec "$PYTHON" -m dep_intel.api
