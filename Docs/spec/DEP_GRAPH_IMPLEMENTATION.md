# Dependency Graph Analysis — Implementation Documentation

> Last updated: August 2025
> Feature area: Code Graph (`/dep-graph.html`, `/api/crg/*`, `/api/cochange`)

---

## Overview

The dependency graph analysis feature builds a per-repository **code knowledge graph** (powered by [code-review-graph](https://github.com/coderabbitai/code-review-graph)) for every repository listed in `repos.txt`. The graph is persisted as a SQLite database in `output/graphs/<slug>/`, queried by an Express REST API, and surfaced in a dedicated UI page at `/dep-graph.html`.

The feature is end-to-end async: the build runs in the background via the `BobCliService` event emitter, streams progress to the browser via SSE, and the UI page polls no state — all data loads lazily on tab visibility.

---

## Architecture

```
repos.txt
   │
   ▼
mcp-server/tools/buildRepoGraph.js         ← ES-module tool, called by both MCP & UI
   │  1. shallow git clone
   │  2. CRG build → CRG postprocess → symlink graph.db
   │  3. git fetch --unshallow  (deepen for commit history)
   │  4. run_cochange.py  (PyDriller) → cochange.json
   │  5. cleanup clone
   │  writes  output/graphs/<slug>/graph.db
   │          output/graphs/<slug>/cochange.json
   │          output/repo_graphs.json  (manifest)
   │
mcp-server/tools/analyzeCommitHistory.js   ← Standalone co-change tool (no CRG needed)
   │  full clone → run_cochange.py → cleanup clone
   │  writes  output/graphs/<slug>/cochange.json
   │          output/cochange_index.json
   │
mcp-server/tools/run_cochange.py           ← PyDriller script (Python)
   │  traverses git log, computes churn + co-change pairs
   │  writes  cochange.json  (per-repo)
   ▼
ui/server.js  /api/crg/*  /api/cochange    ← Express REST API (CJS)
   ▼
ui/public/dep-graph.html                   ← Single-page UI (Bootstrap 5 + vanilla JS)
```

---

## Components

### 1. Graph Build Tool — `mcp-server/tools/buildRepoGraph.js`

Registered as MCP tool `build_repo_graph`. Also imported dynamically by the UI server.

**What it does per repository:**

| Step | Detail |
|------|--------|
| Parse URL | `parseRepoUrl()` from `lib/repoTree.js` extracts owner, repo, host, and injects `GHE_TOKEN`/`GITHUB_TOKEN` into the clone URL |
| Shallow clone | `git clone --depth 1 --single-branch --no-tags` into a unique temp dir (`/tmp/crg-<slug>-XXXX/repo`) |
| Build graph | `code-review-graph build --repo <clone> --data-dir output/graphs/<slug>/ --skip-flows` |
| Post-process | `code-review-graph postprocess --repo <clone> --data-dir output/graphs/<slug>/` — detects communities and execution flows |
| Symlink | Creates `output/graphs/<slug>/.code-review-graph/graph.db → ../graph.db` so all CRG read commands can use `--repo output/graphs/<slug>/` without needing the original clone |
| Unshallow | `git fetch --unshallow --no-tags origin` — deepens the shallow clone so PyDriller can walk full commit history |
| Co-change | Runs `mcp-server/tools/run_cochange.py` via `CRG_PYTHON`. Produces `output/graphs/<slug>/cochange.json`. Scoped to `COCHANGE_SINCE_DAYS` (default: 365). If PyDriller is not installed the step is skipped with a warning — CRG build is **never** blocked. |
| Read stats | `crgStatus()` reads node/edge counts from the persisted DB via `code-review-graph status --json --data-dir` |
| Cleanup | `rmrf(tmpBase)` — temp clone is always deleted (even on failure) via `finally` |
| Manifest | Appends/updates entry in `output/repo_graphs.json` with `{url, slug, dataDir, builtAt, stats, hasCochange}` |

**Environment variables:**

| Variable | Default | Purpose |
|----------|---------|---------|
| `GHE_TOKEN` | — | Token for `github.ibm.com` repos |
| `GITHUB_TOKEN` | — | Token for `github.com` repos |
| `CRG_BIN` | hardcoded venv path | Path to `code-review-graph` binary |
| `CRG_PYTHON` | sibling `python3` of `CRG_BIN` | Python interpreter in the same venv — used for hubs/bridges and co-change |
| `REPOS_FILE` | `../repos.txt` | Override repos list path |
| `OUTPUT_DIR` | `../output` | Override output directory |
| `COCHANGE_SINCE_DAYS` | `365` | Days of history to mine for co-change (0 = all time) |

---

### 1b. Standalone Co-change Tool — `mcp-server/tools/analyzeCommitHistory.js`

Registered as MCP tool `analyze_commit_history`. Also triggered by `POST /api/crg/analyze-commits`.

Runs PyDriller against all repos in `repos.txt` **without** rebuilding CRG graphs. Useful for refreshing commit-history data independently, or when changing the time window.

**What it does per repository:**

| Step | Detail |
|------|--------|
| Clone | Full-history `git clone --single-branch --no-tags` (no `--depth` — PyDriller needs complete history for date-range filtering) |
| Co-change | Runs `run_cochange.py` with `--since-date <ISO>` and `--window-label` derived from `since_days` |
| Cleanup | Temp clone always deleted in `finally` |
| Index | Writes/updates `output/cochange_index.json` |

**Parameters (MCP / API):**

| Parameter | Type | Default | Purpose |
|-----------|------|---------|---------|
| `since_days` | number | 365 | Days of history (90=3 mo, 180=6 mo, 365=1 yr, 0=all) |
| `since_date` | string | — | Explicit ISO date `YYYY-MM-DD` (overrides `since_days`) |
| `max_commits` | number | 0 | Hard cap on commits processed (0 = unlimited) |

---

### 1c. PyDriller Script — `mcp-server/tools/run_cochange.py`

A standalone Python 3.8+ script (no Django/Flask dependency) that:

1. Opens the git repo with `pydriller.Repository(path, since=datetime)`.
2. Walks every commit in the date window.
3. For each commit: records which source files changed, accumulates per-file churn stats, and records every co-changed file pair.
4. Writes `cochange.json` with three sections: `churn`, `cochange`, and `hotspots`.

**Output schema (`cochange.json`):**

```json
{
  "slug":         "owner_repo",
  "repo_url":     "https://...",
  "analysed_at":  "2025-08-31T07:59:02Z",
  "since_date":   "2024-08-31",
  "window_label": "1 year",
  "commit_count": 847,
  "author_count": 12,
  "date_range":   { "first": "2024-09-01", "last": "2025-08-30" },
  "hotspots": [
    { "file": "src/auth.py", "commits": 42, "additions": 890,
      "deletions": 310, "authors": 4, "last_commit_days_ago": 3,
      "churn_score": 50401 }
  ],
  "churn":    [ /* same shape as hotspots, full list */ ],
  "cochange": [
    { "file_a": "src/auth.py", "file_b": "src/session.py",
      "commits": 18, "coupling": 0.43, "is_structural": false }
  ]
}
```

**Coupling score** = `shared_commits / min(commits_a, commits_b)` — a Jaccard-like metric.
`is_structural: false` means neither CRG nor the static call graph knows these files are related — it is a purely historical, **hidden** dependency.

**Install prerequisite:**
```bash
# Install into the same venv as CRG_BIN / CRG_PYTHON
/path/to/.crg-venv/bin/pip install pydriller
```

**Streaming logs:** accepts `onLog(level, message)` callback so the UI server can forward log lines to SSE clients in real time.

---

### 2. MCP Tool Registration — `mcp-server/index.js`

`build_repo_graph` and `analyze_commit_history` are among 11 tools registered on the `dep-intel` MCP server (version `1.2.0`). Both accept optional `repos_file` and `output_dir` parameters and return a `toToolResponse`-shaped result for Bob AI consumption.

---

### 3. Express REST API — `ui/server.js`

All graph endpoints share two helpers:

- **`readManifest()`** — reads `output/repo_graphs.json` synchronously; returns `[]` on missing file.
- **`resolveDataDir(slug)`** — looks up `entry.dataDir` in the manifest for a given slug.
- **`crgJson(args, dataDir)`** — shells out to the CRG CLI, injects `--repo <dataDir>` (or `--data-dir` for `status`), and returns parsed JSON stdout.

#### Endpoints

| Method & Path | Description |
|---------------|-------------|
| `POST /api/crg/build` | Starts background graph build via `importBuildRepoGraph()`. Guards against concurrent runs via `bobCliService.isRunning`. Streams progress through `bobCliService.emit("status", …)` → SSE. |
| `GET /api/crg/repos` | Returns the full manifest array so the UI can populate the repo selector. |
| `GET /api/crg/stats?slug=` | Fetches node/edge/file counts from `status`, plus community count, flow count, function count, and class count in five parallel `Promise.allSettled` calls. |
| `GET /api/crg/search?q=&kind=&slug=` | `crg search <q> [--kind <kind>]` — FTS / semantic search. Returns `nodes[]`. |
| `GET /api/crg/callers?name=&slug=` | `crg query callers_of <name>` — all callers of a symbol. |
| `GET /api/crg/callees?name=&slug=` | `crg query callees_of <name>` — all callees of a symbol. |
| `GET /api/crg/flows?slug=` | `crg flows` — execution flows sorted by criticality. |
| `GET /api/crg/flow?id=&slug=` | `crg flow --id <id>` — single flow with full step list. |
| `GET /api/crg/communities?slug=` | `crg communities` — Leiden-detected code clusters. |
| `GET /api/crg/hubs?slug=` | Python one-liner against `GraphStore`: `find_hub_nodes(store, top_n=10)` — most-connected symbols by total degree. |
| `GET /api/crg/bridges?slug=` | Python one-liner: `find_bridge_nodes(store, top_n=10)` — symbols with highest betweenness centrality. |
| `GET /api/crg/impact?name=&slug=` | Two-step: (1) `crg search <name>` to resolve symbol → absolute file path, (2) `crg impact --files <path>` to compute blast radius. Response strips the temp-clone path prefix from all returned paths. |
| `GET /api/crg/architecture?slug=` | `crg architecture --detail-level minimal` — community list with cohesion and cross-community coupling edges. |
| `GET /api/crg/dead-code?slug=` | `crg dead-code --json --limit 200` — unreferenced functions and classes (no callers, tests, or importers). Paths stripped via `stripCrgPath()`. |
| `GET /api/crg/large-functions?slug=&min_lines=` | `crg large-functions --min-lines N --limit 100` — oversized functions, classes, and files. Paths stripped. Defaults to 50 lines. |
| `GET /api/crg/refactor-suggestions?slug=` | `crg refactor suggest` — community-driven remove/move suggestions. Symbol paths stripped. |
| `GET /api/cochange?slug=&section=&limit=` | Reads `output/graphs/<slug>/cochange.json`. `section` can be `hotspots`, `churn`, `cochange`, or omitted for all. `limit` caps array length (default 50, max 500). Returns 404-style JSON if not yet built. |
| `POST /api/crg/analyze-commits` | Body: `{ since_days: number }`. Triggers standalone co-change analysis (clone → PyDriller → cleanup) via `runAnalyzeCommitHistory`. Streams progress through SSE. |

#### Path stripping helper — `stripCrgPath()` / `stripCrgPathsInArray()`
CRG stores absolute paths from the time the graph was built (pointing into the now-deleted temp clone). A shared regex helper strips the `/crg-<slug>-XXXX/repo/` prefix from all paths returned by `dead-code`, `large-functions`, and `refactor suggest`, normalising them to repo-relative form before the UI receives them.

#### Hubs & Bridges implementation note
Because the CRG CLI does not expose `hub-nodes` and `bridge-nodes` as sub-commands, the server executes a Python one-liner against the SQLite database directly using the `CRG_PYTHON` interpreter from the same virtual environment:

```python
from code_review_graph.graph import GraphStore
from code_review_graph.analysis import find_hub_nodes   # or find_bridge_nodes
import json
store = GraphStore("<dbPath>")
print(json.dumps(find_hub_nodes(store, top_n=10), default=str))
```

---

### 4. BobCliService — `ui/services/bobCliService.js`

A singleton `EventEmitter` that:
- Guards against concurrent builds (`isRunning` flag)
- Maintains a rolling in-memory log buffer (last 1 000 entries)
- Emits `status`, `log`, `complete`, `error`, and `cancelled` events
- Bridges the CJS UI server to the ES-module MCP tools via dynamic `import()`

The `build_repo_graph` flow reuses this service for SSE-streamed progress, consistent with every other tool in the dashboard.

---

### 5. UI Page — `ui/public/dep-graph.html`

A self-contained Bootstrap 5 single-page application.

#### Repo selector
- On load, `loadRepos()` fetches `/api/crg/repos` and populates a `<select>`.
- Auto-selects when only one repo is built.
- `onRepoChange()` sets `activeSlug`, clears `tabLoaded`, reloads stats, and auto-loads the active tab.

#### KPI row
Seven counters populated by `loadStats()` from `/api/crg/stats`:
Nodes · Relationships · Source Files · Functions · Classes · Communities · Exec Flows.

#### Tabs

| Tab | ID | Auto-loads? | Loader function |
|-----|----|-------------|-----------------|
| Search | `tab-search` | No — user-driven | `runSearch()` (debounced, 280 ms) |
| Flows | `tab-flows` | ✅ Yes | `loadFlows()` |
| Communities | `tab-communities` | ✅ Yes | `loadCommunities()` |
| Hubs & Bridges | `tab-hubs` | ✅ Yes | `loadHubs()` |
| Impact Radius | `tab-impact` | No — requires user input | `runImpact()` |
| Architecture | `tab-architecture` | ✅ Yes | `loadArchitecture()` |
| Code Quality | `tab-quality` | ✅ Yes | `loadQuality()` |
| Refactor Intel | `tab-refactor` | ✅ Yes | `loadRefactorSuggestions()` |
| Commit Hotspots | `tab-hotspots` | ✅ Yes | `loadHotspots()` |

#### Tab auto-loading logic

```
tabLoaded : Set<string>   — tracks which pane IDs have fetched data for the current repo

shown.bs.tab  → look up TAB_LOADERS[paneId]
               → if !tabLoaded.has(paneId): add + call loader

onRepoChange  → tabLoaded.clear()  → loadActiveTab()
Refresh btn   → tabLoaded.delete(active.id) → loadActiveTab()   (forces re-fetch)
Per-tab Reload btns → call loader directly (bypasses cache entirely)
```

This means:
- Switching to a tab for the first time fetches its data immediately, without clicking Reload.
- Switching repos re-fetches all tabs on next visit.
- The per-tab **Reload** buttons and the top-level **Refresh** button remain fully functional.

#### Tab features in detail

**Flows tab**
- Lists all execution flows, sorted by criticality score (0–1).
- Colour-coded badges: High / Medium / Low risk.
- Plain-English hint per flow (symbol count, file count, recommended action).
- Expandable call chain: clicking "Show call chain" lazily fetches `/api/crg/flow?id=` (fetched only once per flow per session). Each step shows function name, kind, file path, and line number. Paths are stripped of the temp-clone prefix via `stripTempPath()`.

**Communities tab**
- Leiden-detected clusters rendered as a responsive card grid.
- Each card shows: community name, symbol count, dominant language, cohesion bar (0–100 %), and up to 6 member pill badges.
- Cohesion colour: green ≥ 20 %, amber ≥ 8 %, red below 8 %, with a plain-English description of what the score means.

**Hubs & Bridges tab**
- Side-by-side two-column layout.
- **Hubs**: top 10 by total degree. Degree number colour-coded: rank 1 = red, top 3 = amber, rest = IBM blue.
- **Bridges**: top 10 by betweenness centrality (displayed as 4 d.p.). Values > 0.05 flagged as "significant chokepoint".
- Both lists fetched in parallel via `Promise.allSettled`.

**Search tab**
- Debounced (280 ms) full-text / semantic search via `/api/crg/search`.
- Filterable by kind: All / Function / Class / File / Test.
- Clicking a result opens a detail panel showing **callers** (who calls this?) and **callees** (what does it call?) fetched in parallel.

**Impact Radius tab**
- Accepts any function name, class name, or file path.
- Resolves symbol names to absolute file paths via a preliminary `search` call.
- Displays: resolved symbol card, one-line verdict (Low / Moderate / High blast radius), KPI mini-row (in-file symbols, impacted symbols, impacted files), full impacted-nodes table with depth, and impacted-files list.
- All paths are normalised to repo-relative form by stripping the longest common ancestor prefix.

**Architecture tab**
- Fetches `crg architecture --detail-level minimal` via `/api/crg/architecture`.
- Renders all communities as cards in the same style as the Communities tab.
- Each card gains a "Calls into:" row listing cross-community edges with call counts when present.
- High-coupling warnings from the CRG response are surfaced as an amber callout above the grid.

**Code Quality tab**
- Side-by-side two-column layout; both columns loaded in parallel via `Promise.allSettled`.
- **Dead Code** (left): fetches `/api/crg/dead-code`, groups results by kind (Function / Class). Shows name, file, and line number. Zero-finding state shows a green check. Count badge on the panel header.
- **Large Functions** (right): fetches `/api/crg/large-functions` with a configurable **Min Lines** input (default 50). Ranked list; line-count coloured red > 300, amber > 150, blue otherwise. Count badge on the panel header.
- Changing the Min Lines value and clicking Reload re-fetches with the new threshold.

**Refactor Intel tab**
- Fetches `/api/crg/refactor-suggestions` (`crg refactor suggest`).
- Suggestions grouped into: **Remove** (unused symbols — no callers/tests/importers), **Move** (misplaced symbols), and **Other**.
- Each suggestion shows the type badge, description, affected symbol paths (repo-relative), and the graph-derived rationale.
- A summary callout at the top shows the total count from the CRG response.

**Commit Hotspots tab** *(requires PyDriller — see [SETUP_GUIDE.md](SETUP_GUIDE.md#step-6-install-pydriller))*
- Fetches `/api/cochange?slug=` with the currently selected time window.
- **Time window dropdown**: 3 months / 6 months / **1 year** (default) / 2 years / All time. Changing the dropdown immediately re-loads the cached data for that window (or shows a "rebuild" prompt if data was built under a different window).
- **File Churn Hotspots** (left column): files ranked by `churn_score = commits × (additions + deletions + 1)`. Each row shows a proportional colour bar (red > 75 %, amber > 40 %, blue otherwise), commit count, ± lines, author count, and a staleness badge for files untouched > 90 days.
- **Co-change Pairs** (right column): file pairs ranked by coupling score (0–100 %). A `hidden` badge marks pairs with no static call edge in the CRG graph — these are the most valuable findings (hidden coupling the code graph cannot see).
- Stats pills above the results show: commit count, author count, window label, date range, and analysis timestamp.
- If data has not been built yet, the empty state shows a one-click **Analyse Commit History** button.
- **Action bar button**: "Analyse Commit History" triggers `POST /api/crg/analyze-commits` with the selected `since_days` value — progress streams through the same modal as the graph build.

#### Build flow & SSE progress
- `POST /api/crg/build` → server acknowledges immediately, build runs async.
- `GET /api/events` (SSE) streams `bob_status` / `bob_log` / `bob_complete` / `bob_error` events.
- UI shows a modal with a striped progress bar, current phase label, and a scrollable activity log.
- On completion or error the modal auto-closes after 2–3 seconds and `loadRepos()` + `loadStats()` refresh automatically.

---

## Output files

| File | Written by | Contents |
|------|-----------|---------|
| `output/repo_graphs.json` | `buildRepoGraph.js` | Manifest: array of `{url, displayName, slug, dataDir, builtAt, stats, hasCochange, success}` |
| `output/graphs/<slug>/graph.db` | CRG CLI `build` | SQLite knowledge graph |
| `output/graphs/<slug>/.code-review-graph/graph.db` | `buildRepoGraph.js` (symlink) | Symlink → `../graph.db` for CRG read commands |
| `output/graphs/<slug>/cochange.json` | `run_cochange.py` | Churn scores, co-change pairs, hotspots, date range, window label |
| `output/cochange_index.json` | `analyzeCommitHistory.js` | Per-repo summary: slug, displayName, window_label, since_date, builtAt |

---

## Adding a new repository

1. Append the repo URL to `repos.txt`.
2. Click **Build / Update Graphs** in the UI (or run `build_repo_graph` via Bob AI).
3. The new repo appears in the selector after the build completes.

Incremental updates: the manifest is keyed by slug, so re-running only touches repos that need rebuilding (or all of them — the tool processes the full list each time).
