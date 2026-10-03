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
if [ -x server/.venv/bin/code-review-graph ]; then
  server/.venv/bin/code-review-graph --version
else
  echo "code-review-graph is not installed in server/.venv." >&2
  echo "Install the supported release with: server/.venv/bin/pip install 'code-review-graph>=2.3.8,<2.4'" >&2
fi
