# Cross-repository structural impact

Nami Trace keeps per-repository code-review-graph reports for the existing code-graph page. Structural impact is a separate read of each repository's `.code-review-graph/graph.db`, plus contract bridges from source and from `cluster-manifest.json`.

The published number is a structural impact score: the strongest path's product of relation weights. It is not a calibrated probability that a change will break a caller. Historical co-change stays in `cochange.json`. Commit history is mined with `git log` or the GitHub commits API. PyDriller is installed for the project environment and is not used to train an attention or temporal graph model.

## Supported CRG release

Tested with code-review-graph 2.3.9 and metadata `schema_version` 13. The supported range is `code-review-graph>=2.3.8,<2.4`.

The adapter reads `nodes.kind`, `nodes.qualified_name`, `edges.kind`, `edges.source_qualified`, and `edges.target_qualified`. It does not assume `type`, `source_id`, or `target_id`. Connections are read-only and short. A failed extraction keeps the last good partition and marks it stale.

Install into the project virtualenv:

```bash
server/.venv/bin/pip install 'code-review-graph>=2.3.8,<2.4'
```

`GET /api/health` reports whether the command is present, its version, and whether that version is in the supported range.

## Setup and run

```bash
skills/install-mcp-pydriller/scripts/install.sh
cd web && npm install && cd ..
./scripts/nami-trace.sh
```

`repos.txt` remains the allowlist. `cluster-manifest.json` only enriches repositories that are already listed there. `CLUSTER_MANIFEST` overrides the manifest path. `DEP_INTEL_REFRESH_CHECKOUTS=1` fetches clean tool-owned clones under `output/checkouts`. User worktrees are never reset. Git tokens are passed through a temporary askpass helper and are not placed in clone URLs.

Build graphs for the fixture copy and print the impact report:

```bash
server/.venv/bin/python scripts/demo-cross-repo-impact.py
server/.venv/bin/python scripts/benchmark-cross-repo.py
```

The demo writes `output/demo/out/demo_result.json`. The benchmark writes `output/benchmark_impact.json`.

## Manifest

Schema version 1. Service ids are explicit. Each `repository` value must match a `repos.txt` entry. `graph_db` defaults to `.code-review-graph/graph.db` and must stay inside that repository.

Optional contract declarations:

```json
{
  "contracts": {
    "http": [{"method": "POST", "path": "/api/auth/validate", "version": "", "handler": "src/server.js::validate"}],
    "grpc": [{
      "package": "auth.v1",
      "service": "Auth",
      "method": "Validate",
      "version": "v1",
      "implementation": "src/auth.js::validate",
      "callers": [{"service": "user-service", "symbol": "src/users.js::listUsers"}]
    }],
    "events": [{
      "broker": "kafka",
      "topic": "user.created",
      "schema_version": "1",
      "producers": ["src/users.js::listUsers"],
      "consumers": [{"service": "report-job", "symbol": "report_job.py::total", "group": "reports"}]
    }]
  }
}
```

`expected` entries are declared relationships. They stay distinct from edges extracted from source. Unknown schema versions, duplicate ids, ambiguous repository matches, weights outside `[0,1]`, missing services, and paths that escape a repository are errors. Duplicate repository slugs are reported instead of overwriting outputs.

Relative client URLs stay unresolved unless a `client_bindings` entry or a static base URL identifies one provider. The same route on two hosts matches only the host that resolves.

## Extraction

JavaScript uses Tree-sitter queries over the concrete syntax tree, not regular expressions.

Supported patterns:

- `app.METHOD(path, handler)` and `router.METHOD(path, handler)`
- static `app.use(prefix, router)`, including a relative `require` that exports the router
- named handlers and inline callbacks
- `axios.METHOD(url)` and `axios.create({ baseURL })` when the base URL is a static string
- `fetch(url, { method })`, defaulting to GET only when `method` is absent
- literal URLs and `const` strings; other values stay unresolved

`axios.get`, collection `.get`, comments, and example strings are not Express routes. An inline handler that CRG does not represent becomes a handler anchor. A file-only match is labeled as a file anchor and is not described as an exact function binding.

OpenAPI JSON and YAML documents are parsed. Local `#/components/schemas` refs and relative files in the scanned tree are followed. Remote refs are reported and not fetched. An operation is linked to a handler only when `x-nami-handler` or a manifest handler mapping says so. Compatibility checks cover removed operations, newly required request fields, and removed declared response fields. Other schema edits stay unknown.

Automatic protobuf, gRPC stub, and Kafka discovery are unsupported. Declared gRPC and event contracts are real graph nodes and are traversed by tests. Event identity includes the broker, so the same topic name on two brokers does not meet. Event weight is a ranking heuristic. It does not mean asynchronous changes are safe. Payload impact reaches consumers. Consumer availability, producer availability, and backpressure are rejected as unsupported operational scenarios.

Supported operational scenarios are `http_unavailable` and `grpc_unavailable`.

## Impact

Stored direction and impact direction differ. `CALLS` and `IMPORTS_FROM` are walked backward: a callee or imported symbol affects its callers and importers. `HANDLED_BY` and `IMPLEMENTED_BY` are walked backward with weight 1, so one HTTP or gRPC border weight is applied on the client edge rather than twice. `CONSUMES_HTTP` and `CONSUMES_GRPC` are walked backward with the contract weight. `PRODUCES_EVENT` and `CONSUMED_BY` are walked forward. `CONTAINS` only seeds symbols when the root is a file. `TESTED_BY` is returned as a validation target and is not scored.

`code_change` walks structural dependents. `contract_change` walks consumers and does not re-enter the provider implementation. A client change does not mark the remote provider as affected.

The root score is 1. Later scores multiply weights. The published score is the best path, not a sum. Search uses a maximum-priority queue, epsilon improvements, and nondominated score/hop states so a shorter lower-score path can still extend past a hop budget. Results name the budget that stopped the search.

Deletions are traversed on the previous snapshot. Additions use the new snapshot. Modifications can use the union of the two so a caller removed in the same update is not dropped. A body-only edit is recorded from the source span hash and is not called an API break without contract evidence. If source and the CRG file hash disagree, bounds are unverified.

## HTTP, MCP, and SSE

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/cluster/refresh` | Asynchronous graph job |
| GET | `/api/cluster/graph` | Summary plus a bounded slice |
| GET | `/api/cluster/contracts` | Contract list |
| POST | `/api/cluster/impact` | Impact query |
| GET | `/api/jobs/{id}` | Job status |
| POST | `/api/jobs/{id}/cancel` | Cancel a job |
| GET | `/api/events` | SSE stream |

MCP tools `build_cross_repo_graph`, `get_cross_repo_graph`, `analyze_cross_repo_impact`, and `get_contract_dependencies` call the same engine. HTTP and stdio MCP are separate processes. They coordinate through `output/cluster/generation.json`, which is written last, after the snapshot file. A file lock prevents two publishers from replacing that pair at the same time. In-memory snapshots are not shared across processes.

SSE events are `graph_updated`, `impact_computed`, `job_started`, `job_progress`, `job_failed`, and `job_completed`. Each event has a monotonic id. A full client queue emits `resync` instead of dropping the rest of the stream silently. `Last-Event-ID` replays the buffer; a gap asks the client to reload the snapshot. A failed job does not also emit `job_completed`.

The dashboard Impact page lists readiness, staleness, and diagnostics, runs a query, and shows the score, confidence, and strongest path. Internal edges and cross-service bridges stay distinguishable because the path names the relation and whether the evidence was extracted or declared.

## Process model

FastAPI handlers do not read SQLite on the event loop. Repository reads use a bounded thread pool. The existing CRG MCP client keeps one locked session. Cluster extraction reads SQLite directly and does not remove that lock. The watcher follows `graph.db` and WAL changes, ignores shm-only noise, debounces per service, and schedules another pass when an event arrives during extraction. Quiet-period stabilization is labeled as a heuristic. Only CRG database changes refresh structural topology; the watcher does not run the security or coverage pipeline.

## Measured performance

Recorded on Linux 6.6.87 WSL2, x86_64, Python 3.12.3, by `scripts/benchmark-cross-repo.py`. The graph has 4 services, 10,100 symbols, and 50,200 edges. Results are in `output/benchmark_impact.json`.

| Measurement | Result | Target |
| --- | --- | --- |
| SQLite write | 0.707 s | — |
| SQLite extraction | 1.688 s | — |
| Cold build, including write and extract | 2.562 s | — |
| Incremental one-service refresh | 0.644 s | — |
| Warm traversal p50 | 10.273 ms | — |
| Warm traversal p95 | 11.792 ms | 500 ms |
| Traced Python allocations | 86.82 MiB | — |
| Process peak RSS (`VmHWM`) | 258.61 MiB | 250 MiB |

Warm traversal is under the 500 ms target. Peak RSS is about 9 MiB over 250 MiB on this host. Traced object memory is 86.82 MiB; the rest is the interpreter, allocator arenas, and SQLite during extraction. These figures are measurements, not a guarantee. A 500 ms watcher debounce is not end-to-end latency.

## Fixture demonstration

`scripts/demo-cross-repo-impact.py` copies the three allowlisted fixtures under `output/demo`, builds code-review-graph 2.3.9 for auth-service and user-service, and does not change `fixtures/` or `repos.txt`. The run at `2026-10-03T07:18:43+00:00` published 3 repositories, 18 symbols, 21 edges, 2 HTTP contracts, 11 unresolved references, and 1 diagnostic. report-job has no graph database, so its schema version is empty and its partition is not indexed.

The Express callback at `src/server.js:9` is a handler anchor. CRG does not store that anonymous function. Impact from that anchor reaches `POST /api/auth/validate` at score 1.0, `listUsers` in `src/users.js:6` at 0.8, and `GET /api/users` at 0.8. The user-service file is a further structural dependent at 0.76. A watcher rebuild after a body edit published a new snapshot and `graph_updated`. After the route was removed, the previous snapshot still reached `listUsers`.

## Limitations

- No automatic gRPC or event discovery beyond manifest declarations.
- No operational propagation for event availability or backpressure.
- No claim that a quiet filesystem period proves CRG finished one indexing generation.
- A single in-flight SQLite read finishes its current repository batch before cancellation is observed.
- Remote OpenAPI refs, combinators, and handler links without an explicit mapping stay unresolved.
- Benchmark targets are measurements on the local machine, not a guarantee. Debounce delay is not end-to-end latency.

## Troubleshooting

- Health shows `crg.available: false`: install the pinned code-review-graph release and restart.
- Impact says the manifest repository is not in `repos.txt`: add that checkout to the allowlist. Do not expect route names alone to create a link.
- A service stays stale: the last good partition is still served. Read the extraction error, then refresh after the database is unlocked or replaced.
- The impact response is HTTP 409: the snapshot version changed. Reload and submit the query again.
- SSE sends `resync`: reload `/api/cluster/graph` rather than trusting a partial event stream.
