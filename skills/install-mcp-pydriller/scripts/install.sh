#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname "$0")/../../.." && pwd)
cd "$ROOT"
if [ ! -x server/.venv/bin/python ]; then
  python3 -m venv server/.venv
fi
server/.venv/bin/pip install -e "server[dev]"
server/.venv/bin/python - <<'PY'
import mcp
import pydriller
from dep_intel.mcp_server import main
print(f"mcp {getattr(mcp, '__version__', 'installed')}")
print(f"pydriller {getattr(pydriller, '__version__', 'installed')}")
print(f"dep-intel-mcp entry {main.__module__}")
PY
