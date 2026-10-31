# Intelligence reports

Nami Trace can report three scopes. Workspace and repository reports are observational. They do not return PASS, WARN, BLOCK, or INCOMPLETE. The stored workspace scope name remains `cluster`. The user-facing title is Workspace intelligence. Readiness is Complete when structural intelligence is current and optional sources are available, Partial when structure is current but history or health is incomplete, and Limited when structural intelligence itself is stale or unavailable.

| Scope | Question | Schema |
| --- | --- | --- |
| Change | What changed, what could be affected, and can this change proceed? | `nami.review.export` |
| Repository | What do we know about this service, and where is that view incomplete? | `nami.intelligence.report` |
| Cluster | How are the services connected, and where is workspace intelligence incomplete? | `nami.intelligence.report` |

A repository or cluster report is not a Boundary Gate decision. It does not return pass, warn, block, or incomplete. Review counts inside the report are historical gate outcomes, not a quality score. Gate validation and commentary belong to a change review. They do not replace this report.

## Point in time

`POST /api/reports` reads the published cluster snapshot. It does not rebuild graphs, mine history, refresh topology, scan health, run Nami Rank, or run Boundary Gate.

```json
{ "scope": "cluster" }
```

```json
{ "scope": "repository", "service": "auth-service" }
```

Optional `graph_version` must match the published snapshot. If it does not, generation fails instead of using a newer snapshot. `refresh: true` rebuilds the cluster snapshot first and then reports that resulting version.

Stored reports live under `output/reports/<report-id>/`. Downloads render that stored JSON. They do not analyze again.

## Readiness

| Readiness | Meaning |
| --- | --- |
| Complete | Every included structural partition is current, history is available, and health artifacts are stored. |
| Partial | Structural intelligence is current, and history or health is missing or incomplete. |
| Limited | One or more structural partitions are out of date or failed, or no snapshot exists. |

Missing scans are **Not scanned**. An empty stored scan is **No findings**, not clean. Unavailable means the artifact could not be read.

## Exports

Markdown, JSON, PDF, and an evidence zip. The zip lists its own files in `metadata/manifest.json`. `report_hash` is `sha256:` over canonical JSON. It is an integrity fingerprint, not a signature. Tokens are redacted. The package does not include `.env`, git directories, worktrees, or repository source trees.

## CLI

```bash
server/.venv/bin/python scripts/nami-report.py --cluster
server/.venv/bin/python scripts/nami-report.py --repository auth-service
server/.venv/bin/python scripts/nami-report.py --cluster --refresh
```

Without `--format`, the command prints the report id and the export paths. `--refresh` is the only way the CLI rebuilds the snapshot first.

## MCP

`generate_intelligence_report`, `get_intelligence_report`, and `export_intelligence_report` are additive. Export returns a file path for markdown, JSON, or PDF. It does not return PDF bytes.

## Limits

Relationship rows in the human-readable report are bounded. The total count is kept. Nami Rank is not run to decorate these reports. Automatic protobuf, gRPC, and broker discovery are not claimed. Historical change association is not a dependency.
