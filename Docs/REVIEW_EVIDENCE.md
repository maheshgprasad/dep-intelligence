# Review evidence

Nami Trace helps a reviewer see what a change may affect, why, and whether it is ready to proceed. The explanation is assembled after the existing review pipeline. It does not replace Change View, Nami Rank, or the Boundary Gate decision.

## Layers

| Layer | What it is | What it is not |
| --- | --- | --- |
| Observation | A fact the tools saw, such as a removed route or a failed command | A decision |
| Evidence | A stored record of that fact, with source and location when known | Proof by itself |
| Inference | An operational conclusion linked to evidence ids | A probability |
| Policy | A requirement in `nami-review.yaml` | A gate decision |
| Gate rule | The deterministic reason the gate returned pass, warn, block, or incomplete | A score threshold |
| Action | What a person should do next | A new analysis |

## Evidence records

Evidence is materialized from findings that the existing evidence gate already accepted. Ids are `EV-001`, `EV-002`, and so on, assigned in a stable order for that report. A record may include source, repository, service, file, line, base and head SHA, graph version, freshness, and the claims it supports or contradicts. Missing fields stay absent. The catalog does not invent a line number or a SHA.

Display labels are separate from source ids. `cluster_graph` is shown as “Cross-service dependency graph”. `git_history` is “Repository history”. `verification_command` is “Verification”.

## Sufficiency

`review_sufficiency` grades a candidate claim after the finding exists. Grades are `unsupported`, `weak`, `moderate`, `strong`, and `verified`. The finding generator’s strength is a candidate. This module can downgrade or suppress it. It cannot promote a claim to verified.

| Claim | Required evidence | Ceiling |
| --- | --- | --- |
| Consumed contract break | Contract removal and a known consumer, with current structural evidence | Verified only when those are present and current |
| Required verification failure | The command ran and the exit was non-zero | Verified |
| Forbidden pattern | A changed added line and a policy match | Verified |
| Historical association | History only | Weak |
| Model suggestion | Model text | Weak, and never a blocker |
| Test gap | A changed file without a test change | Advisory; contradicted by a TESTED_BY relation |

Stale structural evidence on a consumed-contract claim becomes moderate. Unavailable structural evidence becomes weak. Temporal-only and model-only claims stay weak.

## Counter-evidence

A claim is suppressed when stored diagnostics contradict it. Examples that the current review already records:

- The reviewed OpenAPI still contains the operation, so a removal claim is not kept.
- A forbidden pattern appears only in a removed block, so it does not block.
- A TESTED_BY relation covers the changed file, so the test-gap finding is not kept.

Suppressed claims stay in the audit section of the report and the evidence package. They are not the default review story.

## Inferences

Inferences are deterministic and reference evidence ids. The set used now is:

- `consumer_compatibility_break` when a verified consumed contract break blocks
- `consumer_compatibility_review` when a breaking contract has no known consumer
- `historical_association_only` for temporal-only candidates
- `structural_and_historical_support` when Nami Rank origin is both
- `structural_dependency_confirmed` for a structural path
- `cross_repository_review_required` for another service on a structural path
- `verification_failed` and `verification_required`
- `test_attention_required`
- `wide_blast_radius` when three or more other services are affected
- `stale_evidence` and `incomplete_evidence`
- `policy_violation` and `security_attention_required`
- `deployment_attention_required` only when enforcement mode is `deployment_gate` and the gate is warn or block

`historical_association_only` never blocks. Confidence on an inference is an evidence grade, not a probability.

## Actions

Required actions are the way to stop a block or complete missing evidence. A consumed contract break asks to restore the API or update the known consumer in the same release. A failed required command asks to fix that command and run the review again. Recommended actions, including Nami Rank review targets, are follow-up and are not blockers.

## Gate checklist

The checklist reports `passed`, `failed`, `warning`, `not_evaluated`, or `unavailable` for dependency evidence, contract compatibility, required verification, and policy. A check is `not_evaluated` when that input was not configured or not run. The report does not invent a passing verification.

## What Nami Rank does here

Nami Rank orders downstream files from current structure and historical change evidence. The review can say a file is a review target. It does not treat the rank as a reason to block.
