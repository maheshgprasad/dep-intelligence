"""Canonical Boundary Gate stages. The backend owns progress; clients do not invent it."""

from __future__ import annotations

REVIEW_STAGES: list[tuple[str, str]] = [
    ("resolve_change", "Selecting the change"),
    ("build_change_view", "Reading the change"),
    ("align_graph", "Checking dependency evidence"),
    ("calculate_impact", "Finding affected services"),
    ("apply_policies", "Checking project rules"),
    ("collect_findings", "Collecting evidence"),
    ("verify_findings", "Running verification"),
    ("gate_decision", "Making the Gate decision"),
    ("write_report", "Writing the report"),
]

STAGE_INDEX = {stage: index for index, (stage, _label) in enumerate(REVIEW_STAGES)}
STAGE_LABELS = dict(REVIEW_STAGES)


def resolve_review_mode(requested: str, alignment: str) -> str:
    """Map a requested review mode to the mode that will actually run.

    ``fast`` and ``exact`` are preserved. ``auto`` uses the published snapshot
    when alignment is exact or compatible, and a private exact graph otherwise.
    """
    choice = requested or "fast"
    if choice in {"fast", "exact"}:
        return choice
    if choice == "auto":
        return "fast" if alignment in {"exact", "compatible"} else "exact"
    return "fast"


def progress_event(stage: str, review_id: str, job_id: str = "") -> dict:
    index = STAGE_INDEX.get(stage, 0)
    count = len(REVIEW_STAGES)
    percent = int((index + 1) / count * 100)
    return {
        "job_id": job_id,
        "review_id": review_id,
        "stage": stage,
        "stage_label": STAGE_LABELS.get(stage, stage),
        "stage_index": index + 1,
        "stage_count": count,
        "percent": percent,
        "operation": "boundary_review",
    }
