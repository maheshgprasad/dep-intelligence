# Review exports

Exports are rendered from the stored review. They do not re-run Nami Rank or the gate.

## Formats

| Format | Endpoint | File |
| --- | --- | --- |
| Markdown | `GET /api/review/{id}/report` | `nami-review-<id>.md` |
| JSON | `GET /api/review/{id}/export/json` | `nami-review-<id>.json` |
| PDF | `GET /api/review/{id}/export/pdf` | `nami-review-<id>.pdf` |
| Evidence package | `GET /api/review/{id}/export/evidence` | `nami-review-<id>-evidence.zip` |

The JSON schema is `nami.review.export`, schema version 1. Later reviews add `gate_validation` and `commentary` without changing the meaning of `gate_decision`, findings, or evidence grades. MCP `export_review` writes Markdown or JSON under `output/reviews/<id>/exports/` and returns the path. It does not return PDF bytes.

Generated files are cached in `output/reviews/<id>/exports/`. The cache key includes the stored report hash and later feedback timestamps. Feedback is listed separately and does not change the stored gate decision.

## Package contents

```text
nami-review-<id>/
  report.md
  report.json
  report.pdf
  evidence/evidence.json
  evidence/counter-evidence.json
  evidence/suppressed-findings.json
  impact/ranked-impact.json
  impact/structural-paths.json
  impact/temporal-evidence.json
  gate/decision.json
  gate/checklist.json
  gate/inferences.json
  gate/rules.json
  gate/actions.json
  verification/verification.json
  change/change-view.json
  metadata/reproducibility.json
  metadata/manifest.json
  validation/gate-validation.json
  validation/invariants.json
  validation/counterfactuals.json
  validation/reproducibility.json
  commentary/commentary.json
```

The package omits `.env`, tokens, worktrees, and repository source trees. Credential-shaped strings are redacted. Changed-line excerpts are not copied into the package.

`gate_input_hash` is a SHA-256 fingerprint of the deterministic decision inputs. It excludes commentary. `report_hash` is a SHA-256 fingerprint of the canonical JSON export, including commentary when a review has it. Neither value is a digital signature.

## Enforcement in the report

Every export states the gate decision, the enforcement mode, and whether that mode would stop a workflow. Advisory mode still shows BLOCK and sets workflow prevented to no. `merge_gate` and `deployment_gate` stop on BLOCK. They stop on INCOMPLETE unless `enforcement.incomplete` is `allow`.

The CLI keeps exit code 0 for pass and warn, 1 for block, and 2 for incomplete. Pass `--enforcement advisory|merge_gate|deployment_gate` to use the mode above instead. The printed gate decision is the same either way.

## PDF

The PDF is a text report: decision, Nami explanation, required actions, gate decision validation, why the decision was made, evidence, counter-evidence, suppressed claims, affected services, verification, limitations, and reproducibility. Ranking internals are not the first page. Markdown includes `Nami explanation` and `Gate decision validation`.

## Impact and research

Impact Explorer can download the ranked candidate table as JSON, CSV, or Markdown. Research can download the evaluation table as JSON or CSV. Those files are the table on screen. Nested gate evidence stays in the review export.

## Intelligence report exports

Repository and cluster reports use schema `nami.intelligence.report`, version 1. Review exports stay on `nami.review.export`.

| Format | Endpoint |
| --- | --- |
| JSON | `GET /api/reports/{id}/export/json` |
| Markdown | `GET /api/reports/{id}/export/markdown` |
| PDF | `GET /api/reports/{id}/export/pdf` |
| Evidence package | `GET /api/reports/{id}/export/evidence` |

These files are rendered from `output/reports/<id>/report.json`. Export does not refresh the graph, run Nami Rank, or run Boundary Gate. The intelligence PDF is a white page with Carbon’s light text, blue accent, and gray rules. Review PDFs stay text-only. See [REPORTING.md](REPORTING.md).
