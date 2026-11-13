# Release loop

These views read stored reviews, the published snapshot, and health artifacts. They do not change Gate decisions or ranking.

## Pull request check

`scripts/nami-pr-check.py --review-id <id>` prints the decision, changed contracts, and affected services. Exit code 1 means `block`. `warn` and `pass` exit 0. The manual workflow is `.github/workflows/nami-gate.yml`. The review must already be stored under `output/reviews`.

## Owners

When the review checkout contains `CODEOWNERS` or `.github/CODEOWNERS`, Review detail lists the last matching owner for each changed file.

## Deployed revision

`output/deployments.json` is optional:

```json
{
  "environment": "production",
  "services": {
    "auth-service": {"sha": "abc123", "at": "2026-11-01T00:00:00Z"}
  }
}
```

## Hygiene

Repository health lists one row per service. `not_scanned` means that repository is absent from `dep_matrix.json` and `package_updates.json`. A repository that was checked and has no newer release is `current`.

## Drift

Reports compares the published contract edges with the latest stored workspace report: added edges, removed edges, unresolved-symbol delta, and newly stale services.

## Notice

Review detail can store a one-line notice. If `NAMI_NOTIFY_WEBHOOK` is an `https://` URL, a `block` is also posted there as `{"text": "..."}`.
