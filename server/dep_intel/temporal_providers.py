"""Temporal evidence providers used by Nami Rank Module (NRM).

CommitHistoryTemporalProvider reads temporal_evidence.json prepared by
analyze_commit_history. Phase1TemporalProvider reads a precomputed JSON
artifact. The Phase-1 PyTorch model is not imported and is not executed here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from dep_intel.commits import lagged_support, parse_cutoff, parse_iso, temporal_rank_score
from dep_intel.evidence_bridge import CanonicalFileRef
from dep_intel.store import read_json

PHASE1_SCHEMA_NOTE = (
    "Phase1TemporalProvider consumes precomputed pair scores. "
    "The continuous-time attention model is not connected in this repository."
)


@dataclass
class PairEvidence:
    source: CanonicalFileRef
    target: CanonicalFileRef
    available: bool
    score: float = 0.0
    support: int = 0
    source_opportunities: int = 0
    target_commits: int = 0
    directional_rate: float = 0.0
    recency: float = 0.0
    support_factor: float = 0.0
    last_supported_at: str | None = None
    median_lag_seconds: float | None = None
    evidence_kind: str = ""
    relation: str = ""
    score_kind: str = "temporal_evidence"
    observation: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "score": self.score,
            "support": self.support,
            "source_opportunities": self.source_opportunities,
            "target_commits": self.target_commits,
            "directional_rate": self.directional_rate,
            "recency": self.recency,
            "support_factor": self.support_factor,
            "last_supported_at": self.last_supported_at,
            "median_lag_seconds": self.median_lag_seconds,
            "evidence_kind": self.evidence_kind,
            "relation": self.relation,
            "score_kind": self.score_kind,
            "observation": self.observation,
            "source": self.source.label() if self.source else "",
            "target": self.target.label() if self.target else "",
        }


class TemporalEvidenceProvider(Protocol):
    name: str

    def pair_evidence(self, source: CanonicalFileRef, target: CanonicalFileRef, as_of: str | None) -> PairEvidence: ...

    def neighbours(self, source: CanonicalFileRef, as_of: str | None, limit: int) -> list[PairEvidence]: ...

    def file_events(self, ref: CanonicalFileRef, as_of: str | None) -> list[datetime]: ...


def _unavailable(source: CanonicalFileRef, target: CanonicalFileRef, kind: str = "") -> PairEvidence:
    return PairEvidence(source=source, target=target, available=False, evidence_kind=kind, score_kind="temporal_evidence")


def _filter_stamp(stamp: str | None, cutoff: datetime | None) -> bool:
    parsed = parse_iso(stamp)
    if parsed is None:
        return cutoff is None
    if cutoff is None:
        return True
    return parsed <= cutoff


class CommitHistoryTemporalProvider:
    """Same-repository directional history from temporal_evidence.json."""

    name = "commit_history"

    def __init__(self, artifacts: dict[str, dict[str, Any]], service_to_slug: dict[str, str]):
        self._artifacts = artifacts
        self._service_to_slug = service_to_slug
        self._slug_to_service: dict[str, str] = {}
        for service, slug in service_to_slug.items():
            self._slug_to_service.setdefault(slug, service)

    def pair_evidence(self, source: CanonicalFileRef, target: CanonicalFileRef, as_of: str | None) -> PairEvidence:
        if self._slug(source) != self._slug(target) or not self._slug(source):
            return _unavailable(source, target)
        cutoff = parse_cutoff(as_of)
        events = self._events(self._slug(source), cutoff)
        support_times = [
            event["committed_at"]
            for event in events
            if source.relative_file_path in event["files"] and target.relative_file_path in event["files"]
        ]
        source_opportunities = sum(1 for event in events if source.relative_file_path in event["files"])
        target_commits = sum(1 for event in events if target.relative_file_path in event["files"])
        if source_opportunities == 0 or not support_times:
            empty = _unavailable(source, target, "same_repo_directional_history")
            empty.source_opportunities = source_opportunities
            empty.target_commits = target_commits
            return empty
        rate = support_times.__len__() / source_opportunities
        last = support_times[-1]
        ranked = temporal_rank_score(rate, last, len(support_times), as_of=cutoff)
        return PairEvidence(
            source=source,
            target=target,
            available=True,
            score=ranked["score"],
            support=len(support_times),
            source_opportunities=source_opportunities,
            target_commits=target_commits,
            directional_rate=rate,
            recency=ranked["recency"],
            support_factor=ranked["support_factor"],
            last_supported_at=last,
            median_lag_seconds=0,
            evidence_kind="same_repo_directional_history",
            relation="same_commit",
            observation="Same-repository commits contained both files. This is directional history, not a probability.",
        )

    def neighbours(self, source: CanonicalFileRef, as_of: str | None, limit: int) -> list[PairEvidence]:
        slug = self._slug(source)
        if not slug or limit <= 0:
            return []
        cutoff = parse_cutoff(as_of)
        events = self._events(slug, cutoff)
        targets: dict[str, None] = {}
        for event in events:
            if source.relative_file_path not in event["files"]:
                continue
            for path in event["files"]:
                if path != source.relative_file_path:
                    targets[path] = None
        ranked = []
        service = source.service_id
        repository = source.repository
        for path in targets:
            target = CanonicalFileRef(service_id=service, repository=repository, relative_file_path=path)
            evidence = self.pair_evidence(source, target, as_of)
            if evidence.available:
                ranked.append(evidence)
        ranked.sort(key=lambda item: (-item.score, item.target.relative_file_path))
        return ranked[:limit]

    def file_events(self, ref: CanonicalFileRef, as_of: str | None) -> list[datetime]:
        cutoff = parse_cutoff(as_of)
        stamps = []
        for event in self._events(self._slug(ref), cutoff):
            if ref.relative_file_path not in event["files"]:
                continue
            parsed = parse_iso(event.get("committed_at"))
            if parsed is not None:
                stamps.append(parsed)
        return stamps

    def _slug(self, ref: CanonicalFileRef) -> str:
        return self._service_to_slug.get(ref.service_id) or ""

    def _events(self, slug: str, cutoff: datetime | None) -> list[dict[str, Any]]:
        artifact = self._artifacts.get(slug) or {}
        rows = []
        for event in artifact.get("events") or []:
            if _filter_stamp(event.get("committed_at"), cutoff):
                rows.append(event)
        rows.sort(key=lambda item: item.get("committed_at") or "")
        return rows


_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


def load_commit_history_provider(output_dir: Path, service_to_slug: dict[str, str]) -> CommitHistoryTemporalProvider:
    artifacts: dict[str, dict[str, Any]] = {}
    graphs = output_dir / "graphs"
    if graphs.is_dir():
        for path in graphs.glob("*/temporal_evidence.json"):
            slug = path.parent.name
            artifacts[slug] = _load_cached(path)
    return CommitHistoryTemporalProvider(artifacts, service_to_slug)


def _load_cached(path: Path) -> dict[str, Any]:
    mtime = path.stat().st_mtime
    key = str(path)
    cached = _CACHE.get(key)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        payload = {}
    _CACHE[key] = (mtime, payload)
    return payload


class Phase1TemporalProvider:
    """Precomputed Phase-1 pair scores. Does not run or import a model."""

    name = "phase1"

    def __init__(self, payload: dict[str, Any] | None):
        self._payload = payload or {}
        self._index: dict[tuple[str, str, str, str], float] = {}
        for row in self._payload.get("predictions") or []:
            key = (
                str(row.get("source_service") or ""),
                str(row.get("source_file") or ""),
                str(row.get("target_service") or ""),
                str(row.get("target_file") or ""),
            )
            try:
                self._index[key] = float(row.get("score") or 0.0)
            except (TypeError, ValueError):
                continue

    def causal_issue(self, as_of: str | None) -> str:
        cutoff = parse_cutoff(as_of)
        if cutoff is None:
            return ""
        artifact_cutoff = parse_cutoff(
            self._payload.get("prediction_cutoff") or self._payload.get("history_cutoff") or self._payload.get("cutoff")
        )
        if artifact_cutoff is None:
            return "phase1_cutoff_unknown"
        if artifact_cutoff > cutoff:
            return "phase1_after_requested_cutoff"
        return ""

    def pair_evidence(self, source: CanonicalFileRef, target: CanonicalFileRef, as_of: str | None) -> PairEvidence:
        if self.causal_issue(as_of):
            return _unavailable(source, target, "phase1_precomputed")
        score = self._index.get(
            (source.service_id, source.relative_file_path, target.service_id, target.relative_file_path)
        )
        if score is None:
            return _unavailable(source, target, "phase1_precomputed")
        clamped = max(0.0, min(1.0, score))
        return PairEvidence(
            source=source,
            target=target,
            available=True,
            score=clamped,
            directional_rate=clamped,
            recency=1.0,
            support_factor=1.0,
            evidence_kind="phase1_precomputed",
            relation="phase1_score",
            observation=PHASE1_SCHEMA_NOTE,
        )

    def neighbours(self, source: CanonicalFileRef, as_of: str | None, limit: int) -> list[PairEvidence]:
        if limit <= 0:
            return []
        rows = []
        for (src_service, src_file, tgt_service, tgt_file), score in self._index.items():
            if src_service != source.service_id or src_file != source.relative_file_path:
                continue
            target = CanonicalFileRef(tgt_service, source.repository if tgt_service == source.service_id else tgt_service, tgt_file)
            evidence = self.pair_evidence(source, target, as_of)
            if evidence.available:
                rows.append(evidence)
        rows.sort(key=lambda item: (-item.score, item.target.service_id, item.target.relative_file_path))
        return rows[:limit]

    def file_events(self, ref: CanonicalFileRef, as_of: str | None) -> list[datetime]:
        return []


def load_phase1_provider(path: Path | None) -> Phase1TemporalProvider:
    if path is None or not path.is_file():
        return Phase1TemporalProvider(None)
    payload = read_json(path.parent, path.name)
    return Phase1TemporalProvider(payload if isinstance(payload, dict) else None)


class MemoryTemporalProvider:
    """In-memory events for tests and the hybrid demo. Same-repo pairs only."""

    name = "memory"

    def __init__(self, events: dict[str, list[dict[str, Any]]], service_to_slug: dict[str, str] | None = None):
        self._events = events
        self._service_to_slug = service_to_slug or {}

    def pair_evidence(self, source: CanonicalFileRef, target: CanonicalFileRef, as_of: str | None) -> PairEvidence:
        return CommitHistoryTemporalProvider(
            {slug: {"events": rows} for slug, rows in self._events.items()},
            self._service_to_slug,
        ).pair_evidence(source, target, as_of)

    def neighbours(self, source: CanonicalFileRef, as_of: str | None, limit: int) -> list[PairEvidence]:
        return CommitHistoryTemporalProvider(
            {slug: {"events": rows} for slug, rows in self._events.items()},
            self._service_to_slug,
        ).neighbours(source, as_of, limit)

    def file_events(self, ref: CanonicalFileRef, as_of: str | None) -> list[datetime]:
        return CommitHistoryTemporalProvider(
            {slug: {"events": rows} for slug, rows in self._events.items()},
            self._service_to_slug,
        ).file_events(ref, as_of)


def structure_conditioned_lag(
    provider: TemporalEvidenceProvider,
    source: CanonicalFileRef,
    target: CanonicalFileRef,
    as_of: str | None,
    window_days: int,
    half_life_days: float,
    matching: str = "one_to_one",
) -> PairEvidence:
    window = timedelta(days=max(0, window_days))
    source_times = provider.file_events(source, as_of)
    target_times = provider.file_events(target, as_of)
    stats = lagged_support(source_times, target_times, window, matching=matching)
    if not stats["source_events"] or not stats["supported_events"]:
        empty = _unavailable(source, target, "structure_conditioned_lagged_history")
        empty.source_opportunities = stats["source_events"]
        empty.observation = (
            "No lagged follow-up within the configured window. "
            "Independent repository histories are not a shared commit event."
        )
        return empty
    cutoff = parse_cutoff(as_of)
    ranked = temporal_rank_score(
        stats["directional_rate"],
        stats["last_supported_at"],
        stats["support"],
        as_of=cutoff,
        half_life_days=half_life_days,
    )
    return PairEvidence(
        source=source,
        target=target,
        available=True,
        score=ranked["score"],
        support=stats["support"],
        source_opportunities=stats["source_opportunities"],
        target_commits=len(target_times),
        directional_rate=stats["directional_rate"],
        recency=ranked["recency"],
        support_factor=ranked["support_factor"],
        last_supported_at=stats["last_supported_at"],
        median_lag_seconds=stats["median_lag_seconds"],
        evidence_kind="structure_conditioned_lagged_history",
        relation="lagged_follow",
        observation=(
            "A change to the source file was historically followed within the configured "
            "time window by a change to structurally dependent target. "
            "This is observational evidence, not causality."
        ),
    )


def provider_for(
    name: str,
    *,
    output_dir: Path,
    service_to_slug: dict[str, str],
    phase1_path: Path | None = None,
) -> TemporalEvidenceProvider:
    key = (name or "commit_history").strip().lower()
    if key in {"commit_history", "history", "temporal"}:
        return load_commit_history_provider(output_dir, service_to_slug)
    if key in {"phase1", "phase-1"}:
        return load_phase1_provider(phase1_path)
    raise ValueError(f"Unknown temporal provider {name!r}")
