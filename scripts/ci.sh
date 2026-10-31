#!/usr/bin/env bash
# Local reproduction of the required GitHub Actions checks.
# Usage: ./scripts/ci.sh backend|semantic|frontend|reports|all
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
mkdir -p output/ci
MODE="${1:-all}"
PYTHON="${PYTHON:-$ROOT/server/.venv/bin/python}"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON="python3"
fi

backend() {
  "$PYTHON" -m pytest server/tests \
    -q \
    --junitxml=output/ci/backend-junit.xml \
    --cov=dep_intel \
    --cov-branch \
    --cov-report=term-missing \
    --cov-report=xml:output/ci/coverage.xml \
    --cov-report=html:output/ci/coverage-html
  "$PYTHON" scripts/check_coverage.py output/ci/coverage.xml .github/quality-baseline.json
}

semantic() {
  "$PYTHON" -m pytest server/tests -m semantic_contract -q --junitxml=output/ci/semantic-junit.xml
  "$PYTHON" scripts/ci_summary.py semantic output/ci/semantic-junit.xml output/ci/semantic-summary.json
}

frontend() {
  cd "$ROOT/web"
  if [[ -f package-lock.json ]]; then
    npm ci
  else
    npm install
  fi
  npm run test:ci
  npm run build
  cd "$ROOT"
  FRONTEND_TEST_RESULT=success FRONTEND_BUILD_RESULT=success \
    "$PYTHON" scripts/ci_summary.py frontend output/ci/frontend-summary.json
}

reports() {
  "$PYTHON" scripts/ci_report_smoke.py --dest output/ci
}

case "$MODE" in
  backend) backend ;;
  semantic) semantic ;;
  frontend) frontend ;;
  reports) reports ;;
  all)
    backend
    semantic
    frontend
    reports
    "$PYTHON" scripts/ci_summary.py summarize output/ci
    ;;
  *)
    echo "Usage: ./scripts/ci.sh backend|semantic|frontend|reports|all" >&2
    exit 2
    ;;
esac
