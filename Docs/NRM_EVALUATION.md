# NRM evaluation

`scripts/evaluate-hybrid.py` compares temporal-only, structural-only, NRM raw, and NRM rank-normalized ranking. `fixtures/hybrid-eval/` is a synthetic demonstration, not a research corpus.

Ground truth is file-level. The changed root file is not a downstream hit. Metrics include Precision@K, Recall@K, F1@K, MAP, MRR, and nDCG@K. The report also stores per-query rows, a bootstrap interval for nDCG (2000 resamples, seed 42), and hybrid-versus-structural wins, ties, and losses.

A historical episode should name `prediction_time`, repository SHAs, the root file, and relevant files. Temporal history must be mined with `until` at that time so `since_days` is anchored to the cutoff. The structural snapshot should be built from those SHAs. The current interactive snapshot is not that checkout; `causal_validity.structural` stays false.

Weights may be searched on validation queries only. Do not tune `alpha` on the final test labels. Sampled GitHub history (30 commits) is for the dashboard and must be labeled sampled. Research claims need a local git history.

The report `reproducibility` block records NRM version, git revision, dataset SHA-256, seed, and platform.
