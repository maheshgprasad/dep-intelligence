#!/usr/bin/env python3
"""Compare a change-only LLM explanation with a Nami-grounded one.

The counts are research measurements of unsupported statements.
They are not production accuracy, failure probability, or AI correctness.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

from dep_intel.config import Settings, load_dotenv  # noqa: E402
from dep_intel.review_commentary import compare_explanations  # noqa: E402
from dep_intel.review_model_provider import model_configured, provider_from_env  # noqa: E402
from dep_intel.review_store import list_reviews, read_review  # noqa: E402

MESSAGE = "AI explanation evaluation requires a configured review model."
COLUMNS = (
    "review_id",
    "condition",
    "generated_claims",
    "unknown_identity_count",
    "invalid_evidence_reference_count",
    "accepted_evidence_linked_claims",
    "rejected_claims",
    "word_count",
    "gate_decision",
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate evidence-grounded AI explanations.")
    parser.add_argument("--review", default="", help="Stored review id")
    parser.add_argument("--all", action="store_true", help="Evaluate every stored review")
    args = parser.parse_args(argv)
    load_dotenv()
    if not model_configured():
        print(MESSAGE)
        return 0
    if not args.review and not args.all:
        print("Choose --review <id> or --all.")
        return 0
    settings = Settings.load()
    reports = _reports(settings.output_dir, args.review, args.all)
    if reports is None:
        print(f"Review {args.review} was not found.")
        return 0
    provider = provider_from_env()
    rows = [compare_explanations(report, provider) for report in reports]
    payload = {
        "schema": "nami.research.ai_explanation_evaluation",
        "schema_version": 1,
        "note": "These counts are not production accuracy, failure probability, or AI correctness.",
        "review_count": len(rows),
        "reviews": rows,
    }
    directory = settings.output_dir / "research"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "ai-explanation-evaluation.json").write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    _write_csv(directory / "ai-explanation-evaluation.csv", rows)
    print(f"Wrote {len(rows)} review comparison{'s' if len(rows) != 1 else ''}.")
    return 0


def _reports(output: Path, review_id: str, all_reviews: bool) -> list[dict] | None:
    if review_id:
        report = read_review(output, review_id)
        return [report] if isinstance(report, dict) else None
    if not all_reviews:
        return []
    found = []
    for row in list_reviews(output, limit=None):
        report = read_review(output, row["review_id"])
        if isinstance(report, dict):
            found.append(report)
    return found


def _write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        for row in rows:
            for condition in ("baseline", "grounded"):
                metrics = row.get(condition) or {}
                writer.writerow({
                    "review_id": row.get("review_id") or "",
                    "condition": condition,
                    "generated_claims": metrics.get("generated_claims") or 0,
                    "unknown_identity_count": metrics.get("unknown_identity_count") or 0,
                    "invalid_evidence_reference_count": metrics.get("invalid_evidence_reference_count") or 0,
                    "accepted_evidence_linked_claims": metrics.get("accepted_evidence_linked_claims") or 0,
                    "rejected_claims": metrics.get("rejected_claims") or 0,
                    "word_count": metrics.get("word_count") or 0,
                    "gate_decision": row.get("gate_decision") or "",
                })


if __name__ == "__main__":
    raise SystemExit(main())
