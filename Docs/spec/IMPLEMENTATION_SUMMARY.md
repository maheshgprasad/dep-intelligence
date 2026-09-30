# Implementation Summary: dep-intel MCP Server v1.2.0

## Overview

The dep-intel MCP server has grown from its original 3-tool implementation to a full 11-tool intelligence platform. This document summarises the current state.

---

## Registered Tools (mcp-server/index.js)

| Tool | File | Output |
|------|------|--------|
| `analyze_dependencies` | `tools/analyzeDeps.js` | `dep_matrix.json` |
| `check_package_updates` | `tools/checkUpdates.js` | `package_updates.json` |
| `review_code` | `tools/reviewCode.js` | `code_review.json` |
| `detect_api_dependencies` | `tools/detectApiDependencies.js` | `api_dependencies.json` |
| `detect_language` | `tools/detectLanguage.js` | `language_detection.json` |
| `run_coverage` | `tools/runCoverage.js` | `test_coverage.json` |
| `scan_vulnerabilities` | `tools/scanVulnerabilities.js` | `vulnerabilities.json` |
| `scan_cve_from_github_issues` | `tools/scanCveFromGithubIssues.js` | `cve_analysis.json` |
| `generate_security_release_report` | `tools/generateSecurityReport.js` | `security_release_report.json` + `.md` |
| `build_repo_graph` | `tools/buildRepoGraph.js` | `repo_graphs.json` + `graphs/<slug>/graph.db` + `graphs/<slug>/cochange.json` |
| `analyze_commit_history` | `tools/analyzeCommitHistory.js` | `graphs/<slug>/cochange.json` + `cochange_index.json` |

---

## Tool: `detect_api_dependencies`

### Features Implemented

1. **OpenAPI/Swagger Parsing**
   - [`parseApiSpec()`](mcp-server/tools/detectApiDependencies.js:95-133) - Extracts API information
   - [`parseBasicYaml()`](mcp-server/tools/detectApiDependencies.js:139-175) - YAML parser
   - Supports OpenAPI 3.x and Swagger 2.0
   - Extracts endpoints, methods, schemas

2. **HTTP Client Detection**
   - [`detectHttpClients()`](mcp-server/tools/detectApiDependencies.js:180-227) - Finds API calls in code
   - **Node.js**: axios, fetch
   - **Go**: http.Client, http.Get/Post/Put/Delete
   - Extracts URLs, methods, line numbers

3. **HTTP Server Detection**
   - [`detectHttpServers()`](mcp-server/tools/detectApiDependencies.js:232-273) - Finds exposed endpoints
   - **Node.js**: Express.js routes
   - **Go**: net/http, Gorilla Mux
   - Extracts paths, methods, frameworks

4. **Service Dependency Graph**
   - [`buildApiDependencyGraph()`](mcp-server/tools/detectApiDependencies.js:278-323) - Maps service relationships
   - Matches client calls to server endpoints
   - Confidence scoring (high/medium/low)

5. **API Spec File Detection**
   - [`isApiSpecFile()`](mcp-server/tools/detectApiDependencies.js:85-93) - Identifies spec files
   - Patterns: `openapi.yaml`, `swagger.json`, `api-spec.yml`, etc.

### Output Schema

```json
{
  "meta": { "generated_at": "...", "source": "...", "repos_file": "..." },
  "api_dependencies": {
    "services": {
      "user-service": {
        "provides": [{ "api": "User Service API", "version": "1.0.0", "endpoints": 5 }],
        "consumes": [{ "url": "/api/auth", "method": "POST", "type": "axios" }],
        "exposes": [{ "path": "/api/users", "method": "GET", "framework": "express" }]
      }
    },
    "dependencies": [
      { "from": "user-service", "to": "auth-service", "type": "api-call", "confidence": "high" }
    ]
  },
  "summary": { "total_services": 3, "total_api_specs": 2, "total_dependencies": 1 }
}
```

---

## Tool: `build_repo_graph`

Registered in `mcp-server/index.js`. Clones every repo in `repos.txt` (shallow), runs `code-review-graph build` + `postprocess`, persists the SQLite DB in `output/graphs/<slug>/`, creates a symlink at `<slug>/.code-review-graph/graph.db`, reads stats, **unshallows the clone and runs PyDriller co-change analysis**, cleans up the clone, and writes `output/repo_graphs.json`.

This tool is also invoked directly by the UI server (`POST /api/crg/build`) to drive the Code Graph page's Build / Update Graphs button with live SSE progress.

The co-change step is non-blocking: if `pydriller` is not installed or the Python script fails, the CRG graph build still succeeds and a warning is logged.

---

## Tool: `analyze_commit_history`

Registered in `mcp-server/index.js`. Standalone tool that clones every repo at full depth, runs `mcp-server/tools/run_cochange.py` (PyDriller) with a configurable `since_days` window, deletes the clone, and writes `output/graphs/<slug>/cochange.json` and `output/cochange_index.json`.

Also triggered by the UI server via `POST /api/crg/analyze-commits` with `{ since_days }` in the request body, streaming progress through SSE.

**Requires:** `pydriller` installed in the venv pointed to by `CRG_PYTHON`:
```bash
/path/to/.crg-venv/bin/pip install pydriller
```

---

## Documentation

| File | Purpose |
|------|---------|
| [`README.md`](../README.md) | Project overview, architecture, full tool table, setup |
| [`Docs/ENHANCED_FEATURES.md`](ENHANCED_FEATURES.md) | Tool usage, schemas, workflows |
| [`Docs/DEP_GRAPH_IMPLEMENTATION.md`](DEP_GRAPH_IMPLEMENTATION.md) | Code Graph API + UI technical reference |
| [`Docs/SETUP_GUIDE.md`](SETUP_GUIDE.md) | Step-by-step environment setup including CRG |
| [`Docs/SKILL_USAGE.md`](SKILL_USAGE.md) | Bob skill and shell script workflow |
| [`Docs/UI_AUTOMATION_GUIDE.md`](UI_AUTOMATION_GUIDE.md) | Dashboard automation and SSE |
| [`.bob/skills/setup-code-graph/SKILL.md`](../.bob/skills/setup-code-graph/SKILL.md) | Auto-invocable skill for CRG setup |

## Quality

- ✅ **Modular**: Each tool is self-contained in `mcp-server/tools/`
- ✅ **Error handling**: Graceful fallbacks and error messages throughout
- ✅ **Mock data**: All network-dependent tools fall back to realistic mock data when tokens are absent
- ✅ **Type safety**: Zod schemas validate all MCP tool inputs
- ✅ **Path safety**: CRG tools strip temp-clone absolute paths before returning them to the UI

---

**Made with Bob** 🤖