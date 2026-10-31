# CI and validation

Nami's CI answers whether the implementation still obeys the design. A passing suite means the deterministic checks below passed for that commit. It does not establish production failure-prediction accuracy.

The validation framework tests implementation consistency and labelled fixtures. It does not claim that Gate decisions are guaranteed correct, that coverage proves correctness, or that a fixture corpus is a production-accuracy result.

## Required pull-request checks

`.github/workflows/ci.yml` runs on pull requests, on pushes to `main`, and on manual dispatch. It uses a read-only `contents` permission. It does not need `OPENAI_API_KEY`, `NAMI_REVIEW_API_KEY`, live GitHub repository data, or a live OSV query.

These check names are stable and are the names to use if branch protection is enabled later:

| Check | What it proves |
| --- | --- |
| Backend test suite | The Python package tests pass on Python 3.11, and line coverage stays at or above the recorded floor. |
| Nami semantic contract | Domain invariants still hold. This suite also runs inside the backend job. The separate job makes the result visible. |
| Frontend checks | Vitest component tests pass and `tsc --noEmit && vite build` succeeds on Node.js 22.22.2. |
| Report and export smoke | Fixture Workspace, Service Cluster, Repository, and Change Intelligence reports export to PDF, Markdown, JSON, and, for intelligence reports, an evidence package. |
| Nami required checks | All four checks above succeeded. This is the single check to require. |
| CI quality summary | Job summary with the counts from that run. It runs even when a required check fails. It is not a merge gate. |

Backend, semantic, frontend, and report smoke run in parallel. The aggregate waits for all four. The summary waits for the aggregate.

## Why Nami has a semantic-contract suite

Line coverage and ordinary unit tests are not enough for a system whose correctness depends on preserving domain distinctions. The semantic-contract tests, marked `semantic_contract`, protect invariants such as:

- Workspace is not Service Cluster.
- Ranking is not evidence.
- Historical association is not structural dependency.
- Validation is not prediction.
- LLM explanation is not enforcement.

They also check that stored intelligence reports stay unchanged after a later review, that unresolved code symbols are not counted as unresolved service providers, that user-facing exports drop local home paths and token-shaped text, and that the product API routes remain present.

Nami's Semantic Contract protects domain invariants that ordinary line coverage cannot guarantee. These tests verify that ranking evidence does not become enforcement evidence, Workspace is not confused with Service Cluster, stored reports remain point-in-time artifacts, and advisory LLM output cannot alter Gate decisions.

## Coverage

`server/pyproject.toml` enables branch coverage for the `dep_intel` package. The only omitted path is `__pycache__`. Production modules are not excluded to raise the number.

`.github/quality-baseline.json` records the measured line coverage and `backend_line_coverage_min`. `scripts/check_coverage.py` fails the backend job when the current `coverage.xml` line rate is below that floor. The allowed drop in the baseline file documents the intended tolerance. Do not lower `backend_line_coverage_min` to make a pull request pass without explaining which behavior stopped being executed. A reduction should be visible in review.

Coverage measures executed lines. It does not establish semantic correctness.

## Report smoke

`scripts/ci_report_smoke.py` builds a temporary fixture:

- `auth-service` calls `user-service`
- `user-service` calls `reporting-service`
- `tooling-repo` has no confirmed call

Workspace scope contains all four. The service cluster contains the three connected services. The repository report is for `auth-service`. A separate Change Intelligence fixture uses the deterministic commentary fallback. Sample files are labelled as CI fixtures. They are not a production analysis.

## Nightly

`.github/workflows/nightly.yml` runs at 02:15 UTC every day and on manual dispatch. It is not required for merge.

It runs the backend tests on Python 3.11 and 3.12, then the demonstrations:

```bash
python scripts/demo-cross-repo-impact.py
python scripts/demo-hybrid-impact.py
python scripts/demo-boundary-gate.py
python scripts/demo-evidence-gate.py
python scripts/demo-gate-validation.py
python scripts/demo-intelligence-report.py
python scripts/evaluate-gate.py
```

The Gate job summary prints the scenario count, decision accuracy, false-block rate, false-pass rate, invariant pass rate, and replay reproducibility from `output/gate-validation/latest.json`. Per-class metrics are included when the evaluator writes them. The summary always includes:

> These figures describe the labelled validation fixture corpus. They are not a production-accuracy guarantee.

A failed nightly run is visible in Actions. It does not change commits that already merged. Investigate the failed job log, then rerun the same script locally from a clean checkout.

The dependency-audit job runs `pip-audit` and `npm audit --audit-level=high`. Findings are visible. That job is not part of `Nami required checks`, because existing ecosystem advisories must not block unrelated development. Review the audit log and upgrade dependencies in their own change.

## Security analysis

`.github/workflows/codeql.yml` analyzes Python and JavaScript/TypeScript on pull requests, pushes to `main`, and a weekly schedule. It is not part of `Nami required checks`. Private repositories may need GitHub Advanced Security before code scanning results appear. A green CodeQL workflow does not mean the application has no vulnerabilities. Results, when the account supports them, appear under Security → Code scanning.

Dependabot opens weekly grouped minor and patch updates for pip (`/server`), npm (`/web`), and GitHub Actions. Those pull requests are not merged automatically. They must pass `Nami required checks`.

## Artifacts

Generated files stay under `output/ci/` and are gitignored with the rest of `output/`. They are not committed.

| Artifact | When | Retention |
| --- | --- | --- |
| `backend-test-results` | Every CI run, including failures | 14 days |
| `semantic-summary` | Every CI run, including failures | 14 days |
| `frontend-summary` | Every CI run, including failures | 14 days |
| `report-smoke` | Every CI run, including failures | 14 days |
| `nami-sample-reports` | `main`, manual dispatch, and failed smoke runs | 14 days |
| `gate-validation-corpus` | Nightly | 14 days |
| `nightly-demo-reports` | Nightly, when the demos write reports | 14 days |

CI does not upload `.env`, tokens, Git credentials, SSH keys, source checkouts, or graph databases. `output/ci/quality-summary.json` describes the CI run. It is not Nami analysis, Boundary Gate evidence, or a stored intelligence report. A future GitHub Pages dashboard could read it. This workflow does not publish Pages.

## Frontend Node.js

Frontend development requires Node.js 22, at least 22.22.2 and below 23. `.nvmrc` is the local version to select. GitHub Actions uses that same `22.22.2` baseline for frontend checks and the nightly npm audit.

```bash
nvm install
nvm use

cd web
npm ci
```

Vitest 4 is required with Vite 8. Vitest 2 does not support Vite 8, and Vitest 4.1 is the smallest current major that declares Vite 6, 7, and 8. The test runner, jsdom environment, and JSON report used by the CI summary stay the same.

Dependabot still opens weekly updates. Minor and patch updates stay in one group. Major updates of `vite`, `@vitejs/plugin-react`, `vitest`, `jsdom`, and `typescript` are grouped together because those packages move as one toolchain. Security updates are not suppressed.

## Run the same checks locally

```bash
./scripts/ci.sh backend
./scripts/ci.sh semantic
./scripts/ci.sh frontend
./scripts/ci.sh reports
./scripts/ci.sh all
```

`backend` is the pytest command used in GitHub, plus the coverage floor. `semantic` runs `pytest server/tests -m semantic_contract`. `frontend` uses `npm ci` when `web/package-lock.json` exists, then `npm run test:ci` and `npm run build`. It stops immediately when Node.js is outside `>=22.22.2 <23`. `reports` runs the fixture smoke script. `all` stops on the first failure. These commands write under `output/ci/` and do not delete the rest of `output/`.

Python 3.11 is the required runtime (`requires-python = ">=3.11"`). The frontend CI image is Node.js 22.22.2.

## Branch protection

Repository files cannot turn on GitHub branch protection. After the workflow has succeeded on `main`, the recommended settings are:

Settings → Rules → Rulesets / Branch protection → `main`

- Require a pull request before merging
- Require status checks to pass
- Require the branch to be up to date before merging
- Require the check named `Nami required checks`
- Block force pushes
- Block deletion of `main`

The four underlying checks stay visible. Requiring the one aggregate name keeps the ruleset stable if a job's internal steps change. Do not require the nightly workflow or CodeQL until those have been stable for this account. CodeQL in particular may be unavailable without Advanced Security.
