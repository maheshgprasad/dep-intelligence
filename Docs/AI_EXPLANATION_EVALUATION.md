# AI explanation evaluation

This experiment asks whether an explanation grounded in a stored Nami review, then checked without another model, produces fewer unsupported statements than an explanation that sees only the change.

The Boundary Gate decision is already stored. The model does not choose PASS, WARN, BLOCK, or INCOMPLETE. Human-readable Gate evidence is built from that stored result and does not require a model.

## Conditions

**A — Baseline.** The model sees the changed service, files, symbols, and contract changes. It does not receive Nami evidence IDs.

**B — Grounded.** The model sees the same change facts plus the stored Gate facts, evidence, inferences, actions, validation, and the human-readable Gate summary. Each claim must cite stored evidence IDs.

A deterministic check then drops a claim when an evidence ID is unknown, an identity was not supplied, the claim contradicts the stored Gate decision, or the wording overclaims safety or certainty. Rejected claim text is not shown in the review PDF.

## Counts

The script records, for each condition:

- generated claims
- unknown identity count
- invalid evidence-reference count
- accepted evidence-linked claims
- rejected claims
- word count

These counts are not production accuracy, failure probability, or AI correctness.

## Run

```bash
python scripts/evaluate-ai-explanations.py --review <id>
python scripts/evaluate-ai-explanations.py --all
```

Results are written to `output/research/ai-explanation-evaluation.json` and `output/research/ai-explanation-evaluation.csv`.

If `NAMI_REVIEW_MODEL_PROVIDER` is `none`, or no review model is configured, the script prints `AI explanation evaluation requires a configured review model.` and exits. Review Detail still shows the deterministic Gate explanation.
