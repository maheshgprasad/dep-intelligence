# Nami Rank Module (NRM)

**Nami Rank Module (NRM) combines structural dependency evidence with temporal/evolutionary evidence to rank downstream software change-impact candidates.**

```text
Structural Evidence ──┐
                      ├──► Nami Rank Module (NRM)
Temporal Evidence ────┘          │
                                 ▼
                     Ranked Impact Candidates
                                 │
                         Evidence Bundle
                                 │
                                MCP
                                 │
                         Cursor / IBM Bob
```

NRM is the Phase-2 research bridge in Nami Trace. It does not replace the structural engine or commit-history analysis. It does not train a model. HTTP path `/api/cluster/hybrid-impact` and MCP tool `analyze_hybrid_impact` are the NRM entry points.

## NRM v2

`module_version` is `2.0`. The ranking unit is a canonical file. Symbol-level CRG paths stay under `structural.impacted_symbols` and `structural.path`. The changed root file is returned in `root` and is not a downstream candidate.

Responses include `causal_validity`. A historical `as_of` can bound temporal evidence, but the published cluster snapshot is the current topology unless a frozen snapshot is supplied. That case is `structural: false` and is not described as fully causal.

Temporal path scores default to the mean of available segments multiplied by path coverage. Cross-repository lag matching defaults to one-to-one. Fusion `raw_weighted` keeps `0.70 S + 0.30 T`. Fusion `rank_normalized` combines within-query percentiles. History windows anchor to `until` when a cutoff exists. Phase-1 artifacts without a cutoff are rejected for historical queries. Evaluation notes are in [NRM_EVALUATION.md](NRM_EVALUATION.md).

Ranking values are not probabilities:

- `structural_score` is the strongest-path product of relation weights.
- `temporal_score` is a directional-history ranking heuristic.
- `hybrid_score` is NRM's weighted mix of those two ranking values.

Timestamp correlation does not establish causality. Independent Git histories are not a shared commit event. Default weights are research parameters and are not claimed to be optimal.

## Problem

Structural impact answers: if this symbol changes, which dependents are reachable in the cluster graph? Historical analysis answers: which files have moved together in Git? Those reports used to stay separate. NRM ranks a union of structurally reachable symbols and historically associated files, with an auditable evidence bundle for each candidate. Cursor and IBM Bob receive that bundle over MCP.

## Structural evidence

Unchanged. `impact.py` still walks relation-aware dependents. HTTP, gRPC, and event contracts stay in the path. `POST /api/cluster/impact` and `analyze_cross_repo_impact` keep their previous semantics. See [CROSS_REPO_IMPACT.md](CROSS_REPO_IMPACT.md).

## Temporal evidence

`analyze_commit_history` still writes `output/graphs/<slug>/cochange.json` with the existing coupling field (`pair_count / min(A, B)`).

It also writes a second artifact, `output/graphs/<slug>/temporal_evidence.json` (`schema_version = 1`), with directional pair evidence:

`directional_rate(A → B) = support(A, B) / occurrences(A)`

`B → A` is computed independently. Optional `until` / `as_of` bounds the history. If no cutoff was supplied, `history.until` is `null`. Full history must not be described as a causal evaluation.

## Entity alignment

`CanonicalFileRef` is `(service_id, repository, relative_file_path)`. Paths are normalized to repository-relative POSIX form. Absolute CRG paths, Windows separators, `./` prefixes, and duplicate slashes are stripped. Files are never joined by basename. HTTP/gRPC/event contract nodes are not treated as source files; they remain on the structural path.

A structural path `validate() → POST /api/auth/validate → listUsers()` compresses to `auth-service:src/server.js → user-service:src/users.js`.

## Structure-conditioned temporal evidence

Same-repository adjacent files use directional same-commit history (`same_repo_directional_history`).

Cross-repository adjacent files on an existing structural path may use lagged follow-up inside a configurable window (default 7 days): if A changed at `t`, did structurally dependent B change in `[t, t + window]`? That is labeled `structure_conditioned_lagged_history`. It is observational. It is not calculated for arbitrary file pairs, and it is not cross-repository co-change.

## NRM scoring

Default research weights: `α = 0.70`, `β = 0.30`.

```
temporal_score = directional_rate × recency × support_factor
recency        = 2 ^ (-age_days / 90)   if last_supported_at exists, else 0
support_factor = min(1, support / 3)
hybrid_score   = α × structural_score + β × temporal_score
```

Missing evidence contributes zero. Scores are clamped to `[0, 1]`. `score_kind` is `temporal_evidence` or `hybrid_rank_score`.

The candidate temporal score is the mean of *available* file-segment scores. Missing segments are omitted from the mean and reported as coverage (`available_segments / total_segments`).

## Candidate union

NRM hybrid mode:

```
structural candidates ∪ temporal neighbours of each root file
```

Default neighbour limit: 25 per changed file. Each row is tagged `structural_only`, `temporal_only`, or `both`.

## Causal cutoff

Query `as_of` filters events at or before that timestamp in the provider. Mining `until` is stored on the artifact. A later commit cannot affect an earlier evaluation. Git uses `--until`. GitHub listing uses the `until` parameter and a second local filter.

## A/B/C evaluation

`python3 scripts/evaluate-hybrid.py` and `python3 scripts/demo-hybrid-impact.py` relaunch `server/.venv/bin/python` so they do not fail on a system interpreter that lacks the project packages.

`scripts/evaluate-hybrid.py` runs three experiments on a labeled dataset:

| Experiment | Mode | Ranking |
| --- | --- | --- |
| A | `temporal_only` | temporal ranking scores |
| B | `structural_only` | structural ranking scores |
| C | `hybrid` (NRM) | `0.70 S + 0.30 T` |

It reports Precision@K, Recall@K, F1@K, MAP, MRR, nDCG@K, and candidate-generation coverage (structural / temporal / union). Generation misses are distinct from ranking misses. `fixtures/hybrid-eval/` is a synthetic demo, not a research corpus.

## Dashboard

The Nami Rank page (`/impact`) calls `POST /api/cluster/hybrid-impact`. Ranking defaults to NRM (structural + temporal). The table shows origin (both / structural / temporal), NRM hybrid score, structural score, and temporal score. Expanding a row shows `why_ranked`, the structural path, and temporal path evidence. Optional **As of** is the causal cutoff. Structural-only and temporal-only remain selectable so the three experiments stay separable. `POST /api/cluster/impact` is unchanged for API clients.

## API and MCP

- `POST /api/cluster/impact` — structural only, unchanged
- `POST /api/cluster/hybrid-impact` — NRM
- MCP `analyze_cross_repo_impact` — structural only
- MCP `analyze_hybrid_impact` — NRM; same `ClusterEngine.hybrid_impact` implementation as HTTP. The JSON payload includes `module`, `module_abbrev`, and `module_description` for Cursor and IBM Bob.

## Phase-1 provider

`Phase1TemporalProvider` reads precomputed JSON. It does not import or run PyTorch. Expected shape:

```json
{
  "model": "phase1-continuous-time-attention",
  "generated_at": "...",
  "cutoff": "...",
  "predictions": [
    {
      "source_service": "...",
      "source_file": "...",
      "target_service": "...",
      "target_file": "...",
      "score": 0.82
    }
  ]
}
```

Place that file at `output/phase1_predictions.json` and set `temporal_provider` to `phase1`. The continuous-time attention model is not connected in this repository.

## Limitations

- Default weights need validation before research conclusions.
- Lagged follow-up is not causality.
- Independent repositories do not share commits.
- GitHub history is sampled (at most 30 commits) and marked `sampled`.
- Contract nodes have no file identity for temporal lookup.
- Phase-1 scores are used only if a precomputed artifact is present.
- NRM ranks with default research weights; those weights are not claimed to be optimal.
