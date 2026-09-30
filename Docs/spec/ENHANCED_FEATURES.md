# Enhanced MCP Server Features

This document describes all tools and capabilities of the dep-intel MCP server (v1.2.0).

---

## Overview

The MCP server includes **11 tools**:

| Tool | Purpose | Output File |
|------|---------|-------------|
| `analyze_dependencies` | Manifest-based dependency matrix | `dep_matrix.json` |
| `check_package_updates` | Package version updates | `package_updates.json` |
| `review_code` | Static code analysis | `code_review.json` |
| `detect_api_dependencies` | REST/API service dependency graph | `api_dependencies.json` |
| `detect_language` | Hybrid language detection (API + clone fallback) | `language_detection.json` |
| `run_coverage` | Real test coverage for Python/Go/JS/TS | `test_coverage.json` |
| `scan_vulnerabilities` | Vulnerable dependency scan (Node/Python/Go/PHP) | `vulnerabilities.json` |
| `scan_cve_from_github_issues` | CVE extraction from Mend GitHub Issues | `cve_analysis.json` |
| `generate_security_release_report` | Release-mapped CVE report from Mend/WhiteSource evidence | `security_release_report.json` + `.md` |
| `build_repo_graph` | Code knowledge graph builder (CRG-backed) | `repo_graphs.json` + `graphs/<slug>/graph.db` |
| `analyze_commit_history` | Commit-history co-change analysis (PyDriller) | `graphs/<slug>/cochange.json` + `cochange_index.json` |

---

## 1. API Dependency Detection

### What It Does

The `detect_api_dependencies` tool discovers REST/API relationships between services:

- **OpenAPI/Swagger Parsing**: Extracts API specifications from YAML/JSON files
- **HTTP Client Detection**: Finds API calls in source code:
  - Node.js: `axios`, `fetch`
  - Go: `http.Client`, `http.Get/Post`
  
- **Server Endpoint Discovery**: Identifies exposed endpoints:
  - Node.js: Express.js routes
  - Go: `net/http`, Gorilla Mux routes

- **Service Dependency Graph**: Maps which services call which APIs
- **Confidence Scoring**: Rates dependency matches (high/medium/low)

### Usage

```bash
bob "@dep-intel detect_api_dependencies"
```

### Output Schema

**File**: `output/api_dependencies.json`

```json
{
  "meta": {
    "generated_at": "2024-01-15T10:30:00.000Z",
    "source": "bob-mcp-live",
    "repos_file": "/path/to/repos.txt"
  },
  "api_dependencies": {
    "services": {
      "user-service": {
        "provides": [
          {
            "api": "User Service API",
            "version": "1.0.0",
            "endpoints": 5
          }
        ],
        "consumes": [
          {
            "url": "/api/auth/validate",
            "method": "POST",
            "type": "axios"
          }
        ],
        "exposes": [
          {
            "path": "/api/users",
            "method": "GET",
            "framework": "express"
          },
          {
            "path": "/api/users/:id",
            "method": "GET",
            "framework": "express"
          }
        ]
      },
      "auth-service": {
        "provides": [
          {
            "api": "Auth Service API",
            "version": "2.1.0",
            "endpoints": 3
          }
        ],
        "consumes": [],
        "exposes": [
          {
            "path": "/api/auth/validate",
            "method": "POST",
            "framework": "express"
          }
        ]
      }
    },
    "dependencies": [
      {
        "from": "user-service",
        "to": "auth-service",
        "type": "api-call",
        "endpoint": "/api/auth/validate",
        "method": "POST",
        "confidence": "high"
      }
    ]
  },
  "summary": {
    "total_services": 3,
    "total_api_specs": 2,
    "total_dependencies": 1,
    "services_with_specs": 2,
    "services_with_clients": 1
  }
}
```

### Supported Technologies

#### API Specifications
- OpenAPI 3.x (YAML/JSON)
- Swagger 2.0 (YAML/JSON)
- Files detected: `openapi.yaml`, `swagger.json`, `api-spec.yml`, etc.

#### HTTP Clients (Node.js)
```javascript
// Detected patterns
axios.get('/api/users')
axios.post('/api/auth', data)
fetch('/api/data')
```

#### HTTP Clients (Go)
```go
// Detected patterns
http.Get("https://api.example.com/users")
http.Post("https://api.example.com/auth", ...)
http.NewRequest("GET", "/api/data", nil)
```

#### HTTP Servers (Node.js)
```javascript
// Express.js routes
app.get('/api/users', handler)
app.post('/api/auth', handler)
```

#### HTTP Servers (Go)
```go
// net/http
http.HandleFunc("/api/users", handler)

// Gorilla Mux
r.Methods("GET").Path("/api/users")
```

### Use Cases

1. **Service Dependency Mapping**: Visualize which services depend on which APIs
2. **Breaking Change Impact**: Identify all consumers before changing an API
3. **API Contract Validation**: Ensure clients match server specifications
4. **Microservice Architecture Analysis**: Understand service relationships
5. **Migration Planning**: Identify dependencies before decomposing monoliths

### Example Workflow

```bash
# Step 1: Detect API dependencies
bob "@dep-intel detect_api_dependencies"

# Step 2: Review the service graph
cat output/api_dependencies.json | jq '.api_dependencies.dependencies'

# Step 3: Check for breaking changes before updating an API
# (Review which services consume the API you're changing)
```

---

## 2. Code Graph Feature (`build_repo_graph`)

The `build_repo_graph` tool clones every repository in `repos.txt`, builds a `code-review-graph` SQLite knowledge graph for each one, and persists the databases in `output/graphs/<slug>/`. This powers the **Code Graph** page at `/dep-graph.html`.

### Usage

```bash
bob "@dep-intel build_repo_graph"
```

Or from the UI: open `http://localhost:3002/dep-graph.html` and click **Build / Update Graphs**.

### What it analyses (9 tabs in dep-graph.html)

| Tab | Data source | Requires |
|-----|-------------|---------|
| Search | `crg search` — FTS/semantic symbol search | CRG graph |
| Flows | `crg flows` — execution flows sorted by criticality | CRG graph |
| Communities | `crg communities` — Leiden-detected code clusters | CRG graph |
| Hubs & Bridges | Python API: `find_hub_nodes` / `find_bridge_nodes` | CRG graph + `CRG_PYTHON` |
| Impact Radius | `crg impact` — blast radius for any symbol or file | CRG graph |
| Architecture | `crg architecture` — cross-community coupling map | CRG graph |
| Code Quality | `crg dead-code` + `crg large-functions` | CRG graph |
| Refactor Intel | `crg refactor suggest` — remove/move suggestions | CRG graph |
| **Commit Hotspots** | `cochange.json` via PyDriller — churn + co-change pairs | PyDriller |

All tabs auto-load on first visit per repo. Reload buttons force a re-fetch.

### Prerequisites

Requires `CRG_BIN` and `CRG_PYTHON` set in `.env`. See [SETUP_GUIDE.md](./SETUP_GUIDE.md) or run the `setup-code-graph` skill.

The **Commit Hotspots** tab additionally requires `pydriller` installed in the CRG venv:
```bash
/path/to/.crg-venv/bin/pip install pydriller
```

---

## 3. Commit History Analysis (`analyze_commit_history`)

The `analyze_commit_history` tool mines git commit history using **PyDriller** to surface two complementary signals that the static code graph cannot provide:

- **File Churn**: which files change most often and most heavily — ranked by `churn_score = commits × (lines_added + lines_deleted + 1)`.
- **Co-change Coupling**: which file pairs always appear in the same commit — a high coupling score reveals hidden dependencies with no corresponding static call edge.

### Usage

```bash
bob "@dep-intel analyze_commit_history"
# With a time window:
bob "@dep-intel analyze_commit_history since_days=180"
```

Or from the UI: open the Code Graph page and click **Analyse Commit History** in the action bar, choosing the time window from the **Commit Hotspots** tab dropdown first.

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `since_days` | number | 365 | Days of history to mine. `90`=3 months, `180`=6 months, `365`=1 year, `0`=all time. |
| `since_date` | string | — | Explicit ISO date `YYYY-MM-DD` — overrides `since_days`. |
| `max_commits` | number | 0 | Hard cap on commits processed per repo. `0` = unlimited. |

### Time window behaviour

The time window is passed to PyDriller's native `since=` parameter, which filters commits at the git traversal level — no post-processing needed. The window is stored in `cochange.json` as `window_label` and `since_date` and shown in the UI stats pills.

Changing the window in the UI dropdown and clicking **Analyse Commit History** writes a fresh `cochange.json` for the selected window. The tab shows an amber warning when the cached data was built under a different window.

### What "hidden coupling" means

When `is_structural: false` appears on a co-change pair, it means:

- The files co-change frequently in commits.
- **Neither** the CRG static graph nor any call/import edge connects them.
- They share a hidden implicit contract — perhaps both implement the same feature, or both touch shared config, or they reflect a cross-cutting concern.

These are the most actionable findings: they reveal architectural debt that pure static analysis misses.

### Output schema (`output/graphs/<slug>/cochange.json`)

```json
{
  "slug":         "owner_repo",
  "repo_url":     "https://github.ibm.com/org/repo",
  "analysed_at":  "2025-08-31T07:59:02Z",
  "since_date":   "2024-08-31",
  "window_label": "1 year",
  "commit_count": 847,
  "author_count": 12,
  "date_range":   { "first": "2024-09-01", "last": "2025-08-30" },
  "hotspots": [
    { "file": "src/auth.py", "commits": 42, "additions": 890,
      "deletions": 310, "authors": 4,
      "last_commit_days_ago": 3, "churn_score": 50401 }
  ],
  "churn":    [ /* full churn list, same shape as hotspots */ ],
  "cochange": [
    { "file_a": "src/auth.py", "file_b": "src/session.py",
      "commits": 18, "coupling": 0.43, "is_structural": false }
  ]
}
```

### Prerequisites

```bash
# Install PyDriller in the CRG venv
/path/to/.crg-venv/bin/pip install pydriller

# Verify
/path/to/.crg-venv/bin/python3 -c "import pydriller; print(pydriller.__version__)"
```

`CRG_PYTHON` in `.env` must point to the same venv. `CRG_BIN` is not required for this tool.

---

## 4. Integration with Existing Tools

### Recommended Full Workflow

```bash
# 1. Detect languages
bob "@dep-intel detect_language"

# 2. Analyze manifest dependencies
bob "@dep-intel analyze_dependencies"

# 3. Check for package updates
bob "@dep-intel check_package_updates"

# 4. Detect API/service dependencies
bob "@dep-intel detect_api_dependencies"

# 5. Review code quality
bob "@dep-intel review_code"

# 6. Run test coverage
bob "@dep-intel run_coverage"

# 7. Scan CVEs from GitHub Issues
bob "@dep-intel scan_cve_from_github_issues"

# 8. Build code knowledge graphs (also runs co-change automatically)
bob "@dep-intel build_repo_graph"

# 9. (Optional) Refresh commit history with a different time window
bob "@dep-intel analyze_commit_history since_days=90"
```

### Tool relationship map

1. **`analyze_dependencies`** → Shows what packages you use
2. **`check_package_updates`** → Shows what needs updating
3. **`detect_api_dependencies`** → Shows service-level dependencies
4. **`review_code`** → Shows static code quality issues
5. **`run_coverage`** → Shows test coverage gaps
6. **`scan_cve_from_github_issues`** → Shows open security vulnerabilities
7. **`generate_security_release_report`** → Maps CVEs to release history
8. **`build_repo_graph`** → Deep structural analysis via code graph
9. **`analyze_commit_history`** → Historical change patterns and hidden coupling

---

## Mock Data Fallback

Most tools include mock data generation when live data is unavailable:

- **No `GHE_TOKEN`**: Tools generate realistic mock data
- **Network Issues**: Graceful fallback to mock data
- **Empty Repos**: Mock data shows example structure

---

## Performance Considerations

### `detect_api_dependencies`
- **Network-Intensive**: Fetches files from GitHub API
- **Rate Limits**: Respects GitHub API rate limits
- **Optimization**: Limits to 20 source files per repo, 5 API specs
- **Timeout**: 15-second timeout per API call

---

## Troubleshooting

| Issue | Cause | Solution |
|-------|-------|----------|
| `dep_matrix.json not found` | `analyze_dependencies` not yet run | Run `analyze_dependencies` first |
| Mock data returned | No `GHE_TOKEN` or network issues | Set `GHE_TOKEN` in environment |
| No API specs found | Repos don't have OpenAPI/Swagger files | Add API spec files to repos |
| Low confidence matches | URL patterns don't match exactly | Review and refine API endpoint patterns |
| Rate limit errors | Too many GitHub API calls | Wait for rate limit reset or use mock data |
| `pydriller is not installed` | PyDriller missing from CRG venv | Run `<CRG_PYTHON> -m pip install pydriller` |
| Commit Hotspots: "not yet built" | Co-change analysis never run | Click **Analyse Commit History** in the UI |
| Co-change shows 0 commits | Window too narrow / no source changes | Widen the time window dropdown or use "All time" |

---

## Future Enhancements

Potential improvements for future versions:

1. **Transitive Dependency Resolution**: Parse actual `node_modules` trees
2. **GraphQL Support**: Detect GraphQL schema dependencies
3. **gRPC Support**: Parse `.proto` files for service definitions
4. **Dependency Visualization**: Generate interactive graphs
5. **Change Impact Simulation**: "What if" analysis for package updates
6. **Integration with CI/CD**: Automated impact checks in pull requests

---

## Contributing

To extend these tools:

1. **Add New Frameworks**: Edit detection patterns in `detectApiDependencies.js`
2. **Improve Scoring**: Adjust impact calculation in `detectDownstreamImpact.js`
3. **Add Metrics**: Extend output schemas with new fields
4. **Optimize Performance**: Add caching or parallel processing

---

## Questions?

See the main [README.md](README.md) for general setup and usage, or check [AGENTS.md](AGENTS.md) for Bob governance rules.

---

**Made with Bob** 🤖