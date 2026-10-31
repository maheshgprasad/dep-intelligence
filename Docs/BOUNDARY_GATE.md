# Nami Boundary Gate

Boundary Gate is the operational review workflow on Review Change and Review Detail. Research is not required to use it.

Nami Boundary Gate (NBG) answers a review question: given an actual change, what changed, what could be affected, which project rules apply, what evidence supports each concern, what can be verified, and should the change pass, warn, block, or stay incomplete?

NBG is not the predictive model. Nami Rank Module (NRM) remains the Phase-2 ranking bridge. It combines structural paths with directional history and ranks downstream files. Those scores are strongest-path and historical ranking evidence. They are not probabilities of breakage, of a later edit, or of failure. NBG reads that ranking to choose review targets and to order findings. It does not rewrite `structural_score`, `temporal_score`, `hybrid_score`, NRM weights, relation weights, history files, or evaluation labels.

## Difference from Nami Rank

Nami Rank asks which downstream files the evidence ranks for a chosen root. Boundary Gate asks what one change means for review and merge safety. The Nami Rank page stays the research view. Boundary Gate is the review workflow at `/review`.

## Architecture

```text
Change source → Change View → structural impact and temporal evidence
    → NRM (existing ClusterEngine.hybrid_impact / analyze_hybrid)
    → policies and review targets → Evidence Gate → verification
    → pass / warn / block / incomplete
```

REST, MCP, and `scripts/review-change.py` call `ReviewEngine`. Fast mode reuses the published cluster snapshot. Exact mode can build a private CRG partition under `output/reviews/<id>/worktrees/` and does not publish that partition as the cluster snapshot.

## Change View

Sources are working tree, staged changes, one commit, a commit range, a GitHub pull request, or an explicit file payload. Git refs are passed as argument arrays. A ref that does not resolve stays unresolved. The view records base and head SHAs, file classifications, hunks, the narrowest changed symbol, and contract changes from the existing JavaScript and OpenAPI extractors. Deletions map through the previous graph when one exists. Graph alignment is `exact`, `compatible`, `stale`, or `unavailable`. A stale graph is not described as exact.

## Context and policy

Context is bounded repository instructions, hunks, and NRM statements. Policy is a rule in `nami-review.yaml`. Supported checks are `forbidden_pattern`, `required_related_test`, `required_companion_file`, `contract_compatibility`, `forbidden_dependency`, `required_command`, `path_restriction`, `documentation_required`, and optional advisory `semantic_review`. An unknown check type fails configuration validation.

## Evidence Gate

Every candidate is checked for a real location, evidence provenance, claim compatibility, staleness, contradiction, and duplicates. A temporal-only association can support a review target. It cannot prove a crash, a dependency, or a contract break. Unsupported findings are dropped. `priority_score` only sorts the remaining findings.

## Verification

Commands come from configuration as argument arrays. The working directory must stay inside the repository. Tokens are stripped from the environment and redacted from output. `allow_network: false` skips commands marked `requires_network`. NBG does not install a network sandbox. A model cannot supply a shell string.

## Gate

`pass` means no blocking verified issue. `warn` means there is review work, including cross-repository impact or a test gap, without a configured block. `block` means a verified critical finding, a verified blocking policy, a failed required verification, or a breaking contract with a known consumer. `incomplete` means a required verification or, in strict graph mode, a stale or missing graph prevented a decision. A high NRM score alone does not block. A temporal-only candidate alone does not block.

## Feedback

Feedback is stored under `output/reviews/`. Statuses are accepted, rejected, false positive, fixed, duplicate, not applicable, and acknowledged risk. Repeated rejection can suggest a policy. It does not create one, and it does not change NRM.

## REST

`POST /api/review/change-view` previews a change and stops before impact, policy, model review, verification, and the gate. `POST /api/review/run` accepts `fast`, `exact`, and `auto`. `fast` and `exact` keep their previous meaning. `auto` resolves to fast when the published graph alignment is exact or compatible, and to exact otherwise. The report records `requested_mode` and `resolved_mode`; `mode` is the mode that ran. Exact and auto return a job id and the review id together. `GET /api/reviews` lists stored review metadata. `GET /api/review/source-options` returns bounded branch and commit metadata. `GET /api/review/{id}`, `GET /api/review/{id}/report`, `POST /api/review/{id}/feedback`, and `GET /api/review/config` are unchanged. `POST /api/jobs/{id}/cancel` cancels a queued or running review. Cancellation is incomplete with reason `cancelled`. It is not a block.

Progress events on the existing SSE stream include `job_id`, `review_id`, `stage`, `stage_label`, `stage_index`, `stage_count`, and `percent`. Stages come from `review_progress.REVIEW_STAGES`. A high NRM rank alone does not block. Temporal-only evidence alone does not block.

## MCP

`build_change_view`, `review_change`, `get_review`, `explain_review_finding`, `record_review_feedback`.

## CLI

`server/.venv/bin/python scripts/review-change.py --repository fixtures/auth-service --working-tree`

Exit `0` for pass or warn, `1` for block, and `2` for incomplete or a configuration failure.

## Configuration

`nami-review.yaml` at the reviewed repository or the project root. The cluster manifest stays the topology file. Model settings, when used, are `NAMI_REVIEW_MODEL_PROVIDER`, `NAMI_REVIEW_MODEL`, `NAMI_REVIEW_BASE_URL`, and `NAMI_REVIEW_API_KEY`. Keys are not written into review artifacts. The deterministic path runs with no key.

## Dashboard

`/review` is Review change. The source control shows only the fields for the selected source. Fast uses the published graph. Exact runs as a background job at `/review/<id>`. The result leads with pass, warn, block, or incomplete, then Findings, Impact, Verification, and Change. Feedback is recorded from the finding detail and does not change ranking scores.

The older static `review_code` scan and the standalone API-dependency report remain available to MCP and tests. They are not dashboard pages. Package, advisory, and coverage scans live under Repository health and are not part of Refresh intelligence.

## Explainability

The gate decision is unchanged by the explanation layer. After `decide_gate`, the report adds an assessment, a checklist, evidence ids (`EV-001` …), inferences (`INF-001` …), actions, and decision rules. Finding generators still propose a candidate strength. `review_sufficiency` can downgrade or suppress that grade. It does not raise a candidate to verified, and it does not read Nami Rank as a block.

Nami Rank still only orders downstream files. A high rank, temporal-only evidence, or a model suggestion cannot block.

| Condition | Evidence required | Outcome |
| --- | --- | --- |
| Consumed contract removed | Removal plus a known consumer, with current structural evidence | BLOCK `BG-CONTRACT-001` |
| Required verification failed | Command executed and a non-zero exit | BLOCK `BG-VERIFY-001` |
| Verified blocking policy violation | Policy match on a changed line | BLOCK `BG-POLICY-001` |
| Verified critical evidence | Policy match and critical severity | BLOCK `BG-CRITICAL-001` |
| Cross-service structural impact | Structural path to another service | WARN `BG-IMPACT-001` |
| Temporal association only | Repository history | Review target `BG-TEMPORAL-001`, never BLOCK |
| Graph stale or unavailable when strict graph is on | Current graph evidence | INCOMPLETE `BG-GRAPH-001` |
| Required verification did not finish | A finished command result | INCOMPLETE `BG-EVIDENCE-001` |
| Model suggestion only | None that the gate will accept | Advisory `BG-AI-001`, never BLOCK |

Stale structural evidence for a consumed-contract claim is downgraded, so it is not a verified block. Strict graph mode still returns INCOMPLETE before that claim can block. Enforcement mode (`advisory`, `merge_gate`, `deployment_gate`) is recorded on the report and does not change the gate decision. The CLI keeps its existing exit codes unless `--enforcement` is set.

## Gate validation

After the decision and its explanation are stored, an independent validator replays the conditions above from the stored findings, verification, alignment, and gate configuration. It does not call the gate implementation again, and it does not change pass, warn, block, or incomplete.

Gate validation is observational and cannot alter the Gate decision.

A mismatch, a blocking rule that lacks its required hard evidence, or a failed safety invariant stays visible as validation failed. Optional model commentary is written after that check and can only describe the stored result. Details: [GATE_VALIDATION.md](GATE_VALIDATION.md).

Details: [REVIEW_EVIDENCE.md](REVIEW_EVIDENCE.md) and [EXPORTS.md](EXPORTS.md).

## Limitations

Automatic protobuf, gRPC, and broker discovery are not claimed. Manifest-declared contracts keep that provenance. Handler body edits are not API breaks. Historical association is not a dependency. Exact review depends on `code-review-graph` to build a private partition. Verification is not a security sandbox. Pull request generation does not post comments.

## Security

Git and verification use argument arrays. Paths that leave the repository are dropped. Review output is size-limited. Credentials are redacted. Tool-owned worktrees live under `output/` and are removed unless `NBG_RETAIN_WORKTREES=1`.

## Demonstration

`server/.venv/bin/python scripts/demo-boundary-gate.py` copies the fixtures, builds graphs, and runs six cases: a body edit, removal of the consumed auth route, an added route, a missing related test, a temporal-only candidate, and a stale graph in strict mode. The removal case is the contract-break block. Nothing in that script edits `fixtures/` or `repos.txt`.

## Research relevance

The A/B/C ranking experiments stay temporal-only, structural-only, and hybrid NRM. NBG is downstream of those experiments. Feedback and gate decisions are not training labels and are not fused back into α or β. A review of a historical range passes the head commit time as `as_of`, so later commits stay outside the temporal evidence. That keeps the Phase-2 ranking result reproducible while giving the same evidence an auditable review decision.
