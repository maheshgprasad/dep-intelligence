"""Offline Gate evaluation reports counts from the labelled corpus."""

from __future__ import annotations

import json
from pathlib import Path

from dep_intel.gate_evaluation import evaluate_corpus, format_table, write_evaluation
from dep_intel.gate_validation_corpus import scenarios


def test_evaluation_reports_the_corpus_without_hard_coded_scores() -> None:
    payload = evaluate_corpus()
    assert payload["schema"] == "nami.gate.validation"
    assert payload["scenario_count"] == len(scenarios())
    assert payload["scenario_count"] >= 16
    labels = payload["confusion_matrix"]["labels"]
    counts = payload["confusion_matrix"]["counts"]
    assert labels == ["pass", "warn", "block", "incomplete"]
    assert len(counts) == 4
    assert all(len(row) == 4 for row in counts)
    metrics = payload["metrics"]
    for key in ("decision_accuracy", "false_block_rate", "false_pass_rate", "rule_activation_accuracy", "evidence_traceability_coverage", "invariant_pass_rate", "replay_reproducibility_rate"):
        assert isinstance(metrics[key], float)
        assert 0.0 <= metrics[key] <= 1.0
    for label in labels:
        for name in ("precision", "recall", "f1"):
            assert isinstance(metrics["per_class"][label][name], float)
    assert isinstance(payload["failures"], list)
    table = format_table(payload)
    assert "expected\\predicted" in table
    assert "false-block rate" in table
    assert payload["definitions"]["false_block_rate"]
    assert payload["definitions"]["false_pass_rate"]


def test_evaluation_artifact_is_written(tmp_path: Path) -> None:
    payload = evaluate_corpus()
    path = write_evaluation(payload, tmp_path)
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["scenario_count"] == payload["scenario_count"]
    assert list((tmp_path / "gate-validation" / "runs").glob("*.json"))
