# Gate validation

Gate validation checks a stored Boundary Gate decision. It does not choose pass, warn, block, or incomplete, and it does not change the decision that was stored.

This is independent deterministic validation: rule replay, evidence requirements, safety invariants, and bounded counterfactual checks. It checks consistency between the available evidence, the configured rules, and the Gate result. It does not prove the future production outcome of the change, and it is not a formal-methods verification of the repositories under review.

## Decision

The gate implementation decides. The validator replays the same conditions from the stored findings, verification results, diagnostics, graph alignment, and gate configuration. `priority_score` is not an input.

```text
I = required evidence is unavailable
    or the review was cancelled
    or configuration failed before the gate
    or strict graph mode and the graph is stale or unavailable
    or a required verification did not finish and policy treats that as incomplete

B = at least one stored finding is a verified blocker
    (not temporal-only, not a review target, not model-sourced, grade verified,
     and a critical finding, a high blocking policy, a consumed contract break,
     or a failed required verification)

W = a non-blocking finding remains, or graph alignment is stale

D = INCOMPLETE    if I
    BLOCK         if not I and B
    WARN          if not I and not B and W
    PASS          otherwise
```

If the replay does not match the stored decision, validation status is `failed`. The stored decision stays as it was.

## Evidence categories

| Category | Sources | Role |
| --- | --- | --- |
| Hard | diff, CRG, cluster graph, OpenAPI, verification, policy, generated or deployment topology, manifest, test | Can support a blocking proof |
| Supporting | repository history, Nami Rank | Ranking or observational context |
| Advisory | model output | Presentation only |

`manifest` and `test` are existing source names for deterministic records. Nami Rank remains ranking evidence. History remains observational. A model suggestion remains advisory.

A blocking rule is not accepted when its sources are only Nami Rank, history, or model output.

## Rule validation

`review_gate_rules.py` lists the evidence each rule id needs. Triggered rules from the stored explanation are checked against that registry. A blocking rule needs its required evidence, the required grade, current structural evidence when the rule says so, at least one hard source, and no decisive counter-evidence.

Validation statuses are `validated`, `validated_with_limitations`, and `failed`. Those words are not the gate decisions.

## Invariants

Ten safety checks run on the stored review:

| Id | Check |
| --- | --- |
| INV-001 | Nami Rank cannot independently block |
| INV-002 | Temporal-only evidence cannot block |
| INV-003 | Model evidence cannot independently block |
| INV-004 | A grade below verified cannot satisfy a verified blocker |
| INV-005 | Contract removal without a known consumer cannot satisfy BG-CONTRACT-001 |
| INV-006 | A passed verification cannot satisfy BG-VERIFY-001 |
| INV-007 | Stale structural evidence cannot satisfy a current-structure rule |
| INV-008 | `priority_score` cannot change the replayed decision |
| INV-009 | Decisive counter-evidence cannot sit beside a validated blocking claim |
| INV-010 | A review target cannot independently block |

## Counterfactuals

For each triggered rule that already validates, the validator changes one stored fact and expects the rule to stop validating. Examples: remove the known consumer, change a failed exit code to zero, downgrade verified evidence to strong, or mark structural evidence stale.

These checks are bounded to triggered rules and a few mutations each. They test rule behavior on the stored inputs. They do not establish what would happen in production if the code change were different, and they do not rerun the graph, history, or Nami Rank.

## Reproducibility

`gate_input_hash` is a SHA-256 fingerprint of the normalized decision inputs: accepted finding fields that affect the decision, the evidence catalogue, verification status, alignment, gate configuration, revisions, and graph version. It excludes commentary, timestamps that do not affect the decision, and presentation order.

`report_hash` fingerprints the exported report, including commentary. Neither hash is a digital signature.

## Where it appears

Repository and cluster intelligence reports stay on their own path (`POST /api/reports`, schema `nami.intelligence.report`). Gate validation is attached to a change review. It does not replace those reports. See [REPORTING.md](REPORTING.md).

The review stores `gate_validation`. The review page shows Decision validation after the Nami explanation. PDF, Markdown, JSON, and the evidence package include the same result. Package files:

```text
validation/gate-validation.json
validation/invariants.json
validation/counterfactuals.json
validation/reproducibility.json
commentary/commentary.json
```

## Experimental corpus

`scripts/evaluate-gate.py` runs the labelled scenarios in `server/dep_intel/gate_validation_corpus.py` and writes `output/gate-validation/latest.json`.

False-block rate: cases whose expected decision is not BLOCK, and whose gate decision is BLOCK, divided by all cases whose expected decision is not BLOCK.

False-pass rate: cases expected to be BLOCK or INCOMPLETE whose gate decision is PASS, divided by all cases whose expected decision is not PASS.

The Research page can show the latest artifact. A review page shows only that review's validation.

## Commentary

Optional model commentary is generated after validation from a bounded fact object. If the model is unavailable, times out, or returns text that fails the checks, Nami uses a deterministic template. Commentary is not a finding and is not evidence. See the review evidence notes for the boundary.

## Limitations

Validation reads the stored review. It does not rebuild a graph or re-execute commands. Missing optional provenance can be a limitation. Invalid required provenance fails validation. A matching replay with a failed safety invariant is still a failed validation. The gate decision is left unchanged in every case.
