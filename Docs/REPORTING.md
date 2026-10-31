# Intelligence reports

Nami Trace reports four scopes. Workspace, service cluster, and repository reports are observational. They do not return PASS, WARN, BLOCK, or INCOMPLETE. Readiness is Complete when structural intelligence is current and optional sources are available, Partial when structure is current but history or health is incomplete, and Limited when structural intelligence itself is stale or unavailable.

| Scope | Contains |
| --- | --- |
| Workspace | Everything in the published snapshot |
| Service Cluster | One connected component of confirmed service calls |
| Repository | One repository/service |
| Change | One Boundary Gate review |

Legacy API value `"cluster"` is an alias for Workspace. A stored report whose scope is `"cluster"` remains a workspace snapshot. It is not reinterpreted as a Service Cluster.

Service Cluster is a Nami reporting and topology concept: a connected component of confirmed cross-service relationships. It is not a Kubernetes cluster. Membership uses confirmed contract calls only. Direction is ignored for connectivity and kept on each displayed call. A repository with no confirmed call is standalone. It stays in the workspace and can have its own repository report. It is not a one-node service cluster.

A workspace, service cluster, or repository report is not a Boundary Gate decision. Workspace, Service Cluster, and Repository readiness describe intelligence completeness. Review counts inside the report are historical gate outcomes, not a quality score. Gate validation and commentary belong to a change review. They do not replace this report.

Workspace human exports list the detected service groups and the standalone repositories. A standalone repository has no confirmed cross-service relationship in that stored snapshot. Service Cluster human exports show the stored cluster label, cluster id, and membership. A review target outside that membership is an external review target. It does not add the service to the cluster.

Nami Rank ranks stored evidence. It is not itself a structural dependency fact. When a warning comes from repository history, the human report names that history as the evidence and names Nami Rank only as ranking context.

## Point in time

`POST /api/reports` reads the published snapshot. It does not rebuild graphs, mine history, refresh topology, scan health, run Nami Rank, or run Boundary Gate. Opening a stored report does not add reviews that were created later. Exports render `report.json` and do not enrich it.

```json
{ "scope": "workspace" }
```

```json
{ "scope": "service_cluster", "cluster_id": "svc-cluster-…" }
```

```json
{ "scope": "repository", "service": "auth-service" }
```

```json
{ "scope": "cluster" }
```

The last body is accepted and stored as a workspace report. The server resolves `cluster_id` from the published snapshot. A browser-supplied service list is not the membership.

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
server/.venv/bin/python scripts/nami-report.py --workspace
server/.venv/bin/python scripts/nami-report.py --service-cluster svc-cluster-…
server/.venv/bin/python scripts/nami-report.py --repository auth-service
server/.venv/bin/python scripts/nami-report.py --cluster
```

`--cluster` is a deprecated alias for `--workspace`.

Without `--format`, the command prints the report id and the export paths. `--refresh` is the only way the CLI rebuilds the snapshot first.

## MCP

`generate_intelligence_report` accepts `workspace`, `service_cluster` (with `cluster_id`), `repository`, and the legacy `cluster` alias. `list_service_clusters` returns membership without the whole graph. `get_intelligence_report` and `export_intelligence_report` read the stored report. Export returns a file path for markdown, JSON, or PDF. It does not return PDF bytes.

## Limits

Relationship rows in the human-readable report are bounded. Connected components, contract consumers, and provenance counts use the full confirmed set. Unresolved provider references come from stored topology discovery. Unresolved code symbols are a code-graph diagnostic and are not provider references. Nami Rank is not run to decorate these reports. Automatic protobuf, gRPC, and broker discovery are not claimed. Historical change association is not a dependency. A stored report is not rewritten when it is opened.
