#!/bin/sh
# Start Nami Trace: one process serves the dashboard and the analysis API.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
cd "$ROOT"

# Vite 8 only runs on Node >=22.22.2 and <23. The shell often still has
# the system Node, so prefer the version pinned in .nvmrc when it is installed.
node_in_range() {
  bin=$1
  raw=$("$bin" -v 2>/dev/null) || return 1
  raw=${raw#v}
  raw=${raw%%-*}
  IFS=. read -r major minor patch <<EOF
$raw
EOF
  case "$major" in
    ''|*[!0-9]*) return 1 ;;
  esac
  case "$minor" in
    ''|*[!0-9]*) return 1 ;;
  esac
  case "${patch:-}" in
    ''|*[!0-9]*) return 1 ;;
  esac
  [ "$major" -eq 22 ] || return 1
  if [ "$minor" -gt 22 ]; then
    return 0
  fi
  [ "$minor" -eq 22 ] && [ "$patch" -ge 2 ]
}

use_frontend_node() {
  req=22.22.2
  if [ -f "$ROOT/.nvmrc" ]; then
    req=$(tr -d '[:space:]' < "$ROOT/.nvmrc")
    req=${req#v}
  fi
  if command -v node >/dev/null 2>&1 && node_in_range "$(command -v node)"; then
    return 0
  fi
  nvm_root=${NVM_DIR:-$HOME/.nvm}
  for dir in \
    "$nvm_root/versions/node/v${req}/bin" \
    "$HOME/.local/node/v${req}/bin"
  do
    if [ -x "$dir/node" ] && node_in_range "$dir/node"; then
      PATH="$dir:$PATH"
      export PATH
      hash -r
      return 0
    fi
  done
  current=$(node -v 2>/dev/null || echo unknown)
  echo "Nami Trace: frontend build needs Node.js >=22.22.2 and <23." >&2
  echo "Detected Node.js ${current}." >&2
  echo "Install the version in .nvmrc, then rerun this script:" >&2
  echo "  nvm install" >&2
  echo "  nvm use" >&2
  exit 1
}

use_frontend_node
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
