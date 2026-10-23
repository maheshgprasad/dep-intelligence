"""Nami Rank Module (NRM).

NRM combines structural dependency evidence with temporal/evolutionary evidence
to rank downstream software change-impact candidates. It calls the existing
structural engine and enriches its candidates. It does not change
structural_score semantics. Ranking scores are not probabilities.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from dep_intel.commits import parse_iso
from dep_intel.evidence_bridge import (
    CanonicalFileRef,
    canonical_file_ref,
    file_backed_chain,
    file_segments,
    lookup_file_nodes,
)
from dep_intel.graph_models import (
    SCORE_KIND,
    SCORE_MEANING,
    AffectedNode,
    GraphNode,
    ImpactResult,
    Snapshot,
)
from dep_intel.temporal_providers import (
    PairEvidence,
    TemporalEvidenceProvider,
    structure_conditioned_lag,
)

MODULE_NAME = "Nami Rank Module"
MODULE_ABBREV = "NRM"
NRM_VERSION = "2.0"
MODULE_DESCRIPTION = (
    "Nami Rank Module (NRM) combines structural dependency evidence with "
    "temporal/evolutionary evidence to rank downstream software change-impact candidates."
)
HYBRID_SCORE_KIND = "hybrid_rank_score"
HYBRID_SCORE_MEANING = (
    "NRM weighted ranking of structural_impact and temporal_evidence. "
    "This is not a probability of breakage or of a future change."
)
HYBRID_LIMITATIONS = [
    SCORE_MEANING,
    "temporal_score is a ranking heuristic (directional rate × recency × support factor), not a probability.",
    HYBRID_SCORE_MEANING,
    "Default weights 0.70 / 0.30 are research parameters and are not claimed to be optimal.",
    "Timestamp correlation does not establish causality.",
    "Independent repository histories are not literal co-change.",
    "Missing evidence contributes zero to the hybrid rank; it is not fabricated.",
    "The Phase-1 continuous-time attention model is not executed in this process.",
]


@dataclass
class HybridSettings:
    alpha_structural: float = 0.70
    beta_temporal: float = 0.30
    half_life_days: float = 90.0
    cross_repo_window_days: int = 7
    temporal_candidate_limit: int = 25
    expand_temporal: bool = True
    experiment: str = "hybrid"
    fusion_method: str = "raw_weighted"
    path_scoring: str = "coverage_adjusted"
    lag_matching: str = "one_to_one"


def analyze_hybrid(
    snapshot: Snapshot,
    structural: ImpactResult,
    provider: TemporalEvidenceProvider,
    *,
    settings: HybridSettings | None = None,
    as_of: str | None = None,
    provider_name: str = "commit_history",
) -> dict[str, Any]:
    import time

    started = time.perf_counter()
    options = settings or HybridSettings()
    alpha = _clamp(options.alpha_structural)
    beta = _clamp(options.beta_temporal)
    if options.fusion_method == "rank_normalized":
        beta = _clamp(1.0 - alpha)
    root_refs = _root_refs(snapshot, structural)
    root_tokens = {ref.token() for ref in root_refs}
    files: dict[tuple[str, str, str], dict[str, Any]] = {}
    structural_started = time.perf_counter()
    for item in structural.affected:
        node = snapshot.nodes.get(item.key)
        ref = canonical_file_ref(node, snapshot) if node is not None else None
        if ref is None or ref.token() in root_tokens:
            continue
        slot = files.setdefault(ref.token(), _empty_file(ref))
        slot["symbols"].append(item)
        if item.score >= slot["structural_score"]:
            slot["structural_score"] = item.score
            slot["strongest"] = item
            slot["root_key"] = _path_root(item, structural)
    structural_ms = (time.perf_counter() - structural_started) * 1000
    temporal_started = time.perf_counter()
    for slot in files.values():
        strongest = slot["strongest"]
        chain = file_backed_chain(snapshot, slot["root_key"], strongest.path if strongest else [])
        segments = _segment_evidence(chain, provider, as_of, options)
        slot["segments"] = segments
        slot["structural_available"] = True
    if options.expand_temporal and options.experiment != "structural_only":
        for ref in root_refs:
            for neighbour in provider.neighbours(ref, as_of, options.temporal_candidate_limit):
                if neighbour.target.token() in root_tokens:
                    continue
                token = neighbour.target.token()
                named = lookup_file_nodes(snapshot, neighbour.target)
                slot = files.get(token)
                if slot is None:
                    slot = _empty_file(neighbour.target)
                    slot["structural_available"] = False
                    slot["segments"] = [neighbour]
                    slot["temporal_direct"] = neighbour
                    slot["display_name"] = named[0].name if named else ""
                    files[token] = slot
                elif not any(segment.available for segment in slot["segments"]):
                    slot["segments"] = [neighbour]
                    slot["temporal_direct"] = neighbour
    temporal_ms = (time.perf_counter() - temporal_started) * 1000
    rows = [_file_row(slot, snapshot, alpha, beta, options) for slot in files.values()]
    if options.experiment == "temporal_only":
        rows = [row for row in rows if row["temporal"]["available"]]
    if options.experiment == "structural_only":
        rows = [row for row in rows if row["structural"]["available"]]
    _apply_fusion(rows, alpha, beta, options)
    rows.sort(
        key=lambda row: (
            -row["hybrid_score"],
            -row["structural"]["score"],
            -row["temporal"]["score"],
            row["service_id"],
            row["file_path"] or "",
            row["sort_key"],
        )
    )
    for index, row in enumerate(rows, start=1):
        row["rank"] = index
    diagnostics = [item.to_dict() for item in structural.diagnostics]
    phase1_issue = ""
    if as_of and hasattr(provider, "causal_issue"):
        phase1_issue = provider.causal_issue(as_of) or ""
        if phase1_issue == "phase1_cutoff_unknown":
            diagnostics.append(
                {
                    "code": "phase1_cutoff_unknown",
                    "severity": "warning",
                    "message": "The Phase-1 artifact has no training/prediction cutoff and cannot be used for causal historical evaluation.",
                }
            )
            rows = []
        elif phase1_issue:
            diagnostics.append(
                {
                    "code": phase1_issue,
                    "severity": "warning",
                    "message": "The Phase-1 artifact cutoff is after the requested as_of, so its scores are not used.",
                }
            )
            rows = []
    temporal_valid = not phase1_issue
    structural_valid = not as_of
    if as_of:
        diagnostics.append(
            {
                "code": "temporal_cutoff",
                "message": f"Temporal evidence uses history at or before {as_of}. Later commits are excluded. Ages are relative to this cutoff.",
                "severity": "info",
            }
        )
        diagnostics.append(
            {
                "code": "structural_snapshot_not_historically_aligned",
                "severity": "warning",
                "message": "The structural graph is the current snapshot, not a checkout at the requested cutoff.",
            }
        )
    fusion_ms = 0.0
    total_ms = (time.perf_counter() - started) * 1000
    primary_root = root_refs[0] if root_refs else None
    return {
        "analysis_id": str(uuid.uuid4()),
        "structural_analysis_id": structural.analysis_id,
        "graph_version": structural.graph_version,
        "mode": structural.mode,
        "roots": list(structural.roots),
        "root": {
            "service_id": primary_root.service_id if primary_root else "",
            "repository": primary_root.repository if primary_root else "",
            "file_path": primary_root.relative_file_path if primary_root else "",
            "symbols": [snapshot.nodes[key].name for key in structural.roots if key in snapshot.nodes],
        },
        "ranking_unit": "file",
        "weights": {
            "structural": alpha,
            "temporal": beta,
            "research_defaults": alpha == 0.70 and beta == 0.30,
            "note": "Default research parameters. Not claimed to be optimal.",
        },
        "fusion": {"method": options.fusion_method, "weights": {"structural": alpha, "temporal": beta}},
        "temporal_provider": provider_name,
        "temporal_cutoff": as_of,
        "temporal_context": _temporal_context(provider, provider_name, as_of, temporal_valid),
        "structural_context": {
            "graph_version": structural.graph_version,
            "generated_at": snapshot.generated_at,
            "workspace_heads": {part.service_id: part.workspace_head for part in snapshot.partitions.values()},
            "requested_cutoff": as_of,
            "historically_aligned": False,
        },
        "causal_validity": {
            "temporal": temporal_valid,
            "structural": structural_valid,
            "overall": temporal_valid and structural_valid,
        },
        "experiment": options.experiment,
        "module": MODULE_NAME,
        "module_abbrev": MODULE_ABBREV,
        "module_version": NRM_VERSION,
        "module_description": MODULE_DESCRIPTION,
        "score_kind": HYBRID_SCORE_KIND,
        "score_meaning": HYBRID_SCORE_MEANING,
        "structural_score_kind": SCORE_KIND,
        "structural_score_meaning": SCORE_MEANING,
        "candidates": rows,
        "diagnostics": diagnostics,
        "limitations": list(structural.limitations) + [item for item in HYBRID_LIMITATIONS if item not in structural.limitations],
        "truncated": structural.truncated,
        "truncation_reason": structural.truncation_reason,
        "performance": {
            "structural_ms": round(structural_ms, 1),
            "temporal_ms": round(temporal_ms, 1),
            "fusion_ms": round(fusion_ms, 1),
            "total_ms": round(total_ms, 1),
        },
    }


def _segment_evidence(
    chain: list[tuple[GraphNode, CanonicalFileRef]],
    provider: TemporalEvidenceProvider,
    as_of: str | None,
    options: HybridSettings,
) -> list[PairEvidence]:
    segments: list[PairEvidence] = []
    for source, target in file_segments(chain):
        if source.service_id == target.service_id:
            segments.append(provider.pair_evidence(source, target, as_of))
            continue
        segments.append(
            structure_conditioned_lag(
                provider,
                source,
                target,
                as_of,
                options.cross_repo_window_days,
                options.half_life_days,
                options.lag_matching,
            )
        )
    return segments


def _root_refs(snapshot: Snapshot, structural: ImpactResult) -> list[CanonicalFileRef]:
    refs: list[CanonicalFileRef] = []
    seen: set[tuple[str, str, str]] = set()
    for key in structural.roots:
        node = snapshot.nodes.get(key)
        ref = canonical_file_ref(node, snapshot) if node is not None else None
        if ref is None or ref.token() in seen:
            continue
        seen.add(ref.token())
        refs.append(ref)
    return refs


def _path_root(item: AffectedNode, structural: ImpactResult) -> str:
    if item.path:
        return item.path[0].source
    if item.key in structural.roots:
        return item.key
    return structural.roots[0] if structural.roots else item.key


def _empty_file(ref: CanonicalFileRef) -> dict[str, Any]:
    return {
        "ref": ref,
        "symbols": [],
        "structural_score": -1.0,
        "strongest": None,
        "root_key": "",
        "segments": [],
        "structural_available": False,
        "temporal_direct": None,
    }


def _file_row(slot: dict[str, Any], snapshot: Snapshot, alpha: float, beta: float, options: HybridSettings) -> dict[str, Any]:
    ref: CanonicalFileRef = slot["ref"]
    strongest: AffectedNode | None = slot["strongest"]
    segments: list[PairEvidence] = slot["segments"]
    direct: PairEvidence | None = slot["temporal_direct"]
    available = [segment for segment in segments if segment.available]
    raw = sum(segment.score for segment in available) / len(available) if available else 0.0
    coverage = (len(available) / len(segments)) if segments and direct is None else None
    if direct is not None:
        temporal_score = direct.score
        coverage = None
    elif options.path_scoring == "coverage_adjusted" and coverage is not None:
        temporal_score = raw * coverage
    else:
        temporal_score = raw
    structural_available = bool(slot["structural_available"])
    temporal_available = bool(available)
    opportunities = max((segment.source_opportunities for segment in segments), default=0)
    if temporal_available:
        status = "supported"
    elif opportunities > 0:
        status = "unsupported"
    else:
        status = "unavailable"
    if structural_available and temporal_available:
        origin = "both"
    elif structural_available:
        origin = "structural_only"
    else:
        origin = "temporal_only"
    structural_score = slot["structural_score"] if structural_available and slot["structural_score"] >= 0 else 0.0
    hybrid = _hybrid_score(alpha, beta, structural_score, temporal_score, structural_available, temporal_available, options.experiment)
    quality = _evidence_quality(temporal_available, opportunities, max((segment.support for segment in available), default=0), coverage)
    impacted = [
        {
            "name": item.name,
            "qualified_name": item.qualified_name,
            "line_start": item.line_start,
            "kind": item.kind,
            "structural_score": item.score,
        }
        for item in slot["symbols"]
    ]
    representative = max(available, key=lambda segment: segment.score) if available else None
    return {
        "key": {"service_id": ref.service_id, "repository": ref.repository, "file_path": ref.relative_file_path},
        "sort_key": f"{ref.service_id}:{ref.relative_file_path}",
        "rank": 0,
        "service_id": ref.service_id,
        "name": strongest.name if strongest is not None else (slot.get("display_name") or ref.relative_file_path.rsplit("/", 1)[-1]),
        "qualified_name": strongest.qualified_name if strongest is not None else ref.relative_file_path,
        "file_path": ref.relative_file_path,
        "line_start": strongest.line_start if strongest is not None else None,
        "category": "file",
        "kind": "File",
        "hybrid_score": hybrid,
        "evidence_origin": origin,
        "structural": {
            "available": structural_available,
            "score": structural_score,
            "aggregation": "max_symbol_path",
            "score_kind": SCORE_KIND,
            "hops": strongest.hops if strongest is not None else 0,
            "path": [step.to_dict() for step in strongest.path] if strongest is not None else [],
            "strongest_symbol": (
                {
                    "name": strongest.name,
                    "qualified_name": strongest.qualified_name,
                    "line_start": strongest.line_start,
                }
                if strongest is not None
                else None
            ),
            "impacted_symbols": impacted,
        },
        "temporal": {
            "available": temporal_available,
            "status": status,
            "score": temporal_score,
            "raw_score": raw,
            "coverage": coverage,
            "coverage_adjusted_score": None if coverage is None else raw * coverage,
            "evidence_quality": quality,
            "evidence_kind": representative.evidence_kind if representative else "",
            "support": representative.support if representative else 0,
            "source_opportunities": representative.source_opportunities if representative else opportunities,
            "directional_rate": representative.directional_rate if representative else 0.0,
            "recency": representative.recency if representative else 0.0,
            "aggregation_method": "direct_neighbour" if direct is not None else "mean_available_then_coverage_adjusted",
            "temporal_path_evidence": [segment.to_dict() for segment in segments],
            "path_segments": [segment.to_dict() for segment in segments],
            "observation": representative.observation if representative else "",
        },
        "why_ranked": _file_why(ref, strongest, origin, structural_score, temporal_score, coverage, quality, alpha, beta),
        "fusion": {"method": options.fusion_method, "structural_raw": structural_score, "temporal_raw": temporal_score},
    }


def _evidence_quality(available: bool, opportunities: int, support: int, coverage: float | None) -> str:
    if not available and opportunities <= 0:
        return "unavailable"
    if opportunities < 2 and support < 1:
        return "insufficient"
    covered = coverage is None or coverage >= 0.75
    if available and support >= 3 and opportunities >= 5 and covered:
        return "high"
    if available and support >= 2:
        return "medium"
    if available:
        return "low"
    return "insufficient"


def _file_why(ref: CanonicalFileRef, strongest: AffectedNode | None, origin: str, structural: float, temporal: float, coverage: float | None, quality: str, alpha: float, beta: float) -> list[str]:
    facts = [f"Ranked file {ref.label()}."]
    if strongest is not None:
        facts.append(f"Strongest structural path score: {structural:.2f} via {strongest.name}.")
    if origin == "temporal_only":
        facts.append("Included from historical change evidence. Not structurally reachable in this snapshot.")
    elif origin == "structural_only":
        facts.append("No usable temporal evidence was available. Ranked from structural dependency evidence.")
    if coverage is not None:
        facts.append(f"Temporal path coverage {coverage:.2f}. Evidence quality: {quality}.")
    facts.append(f"NRM score uses {alpha:.0%} structural and {beta:.0%} temporal ranking evidence. This is not a probability of breakage.")
    return facts


def _apply_fusion(rows: list[dict[str, Any]], alpha: float, beta: float, options: HybridSettings) -> None:
    if options.fusion_method != "rank_normalized" or not rows:
        return
    structural_norm = _percentiles([row["structural"]["score"] for row in rows])
    temporal_norm = _percentiles([row["temporal"]["score"] for row in rows])
    for index, row in enumerate(rows):
        row["fusion"]["method"] = "rank_normalized"
        row["fusion"]["structural_normalized"] = structural_norm[index]
        row["fusion"]["temporal_normalized"] = temporal_norm[index]
        row["hybrid_score"] = _hybrid_score(
            alpha,
            beta,
            structural_norm[index],
            temporal_norm[index],
            row["structural"]["available"],
            row["temporal"]["available"],
            options.experiment,
        )


def _percentiles(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda index: (values[index], index))
    ranks = [0.0] * len(values)
    for place, index in enumerate(order):
        ranks[index] = 1.0 if len(values) == 1 else place / (len(values) - 1)
    return ranks


def _temporal_context(provider: TemporalEvidenceProvider, name: str, as_of: str | None, causal: bool) -> dict[str, Any]:
    return {
        "provider": name,
        "requested_cutoff": as_of,
        "history_since": None,
        "history_until": None,
        "complete": True,
        "sampled": False,
        "causal_for_requested_cutoff": causal,
    }


def _hybrid_score(
    alpha: float,
    beta: float,
    structural: float,
    temporal: float,
    structural_available: bool,
    temporal_available: bool,
    experiment: str,
) -> float:
    if experiment == "structural_only":
        return _clamp(structural if structural_available else 0.0)
    if experiment == "temporal_only":
        return _clamp(temporal if temporal_available else 0.0)
    s = structural if structural_available else 0.0
    t = temporal if temporal_available else 0.0
    return _clamp(alpha * s + beta * t)


def _structural_payload(item: AffectedNode) -> dict[str, Any]:
    return {
        "available": True,
        "score": item.score,
        "hops": item.hops,
        "path": [step.to_dict() for step in item.path],
        "score_kind": SCORE_KIND,
    }


def _temporal_payload(
    evidence: PairEvidence | None,
    score: float,
    available: bool,
    segments: list[PairEvidence],
) -> dict[str, Any]:
    total = len(segments)
    found = sum(1 for item in segments if item.available)
    coverage = (found / total) if total else 0.0
    body = {
        "available": available,
        "score": score if available else 0.0,
        "score_kind": "temporal_evidence",
        "support": evidence.support if evidence else 0,
        "directional_rate": evidence.directional_rate if evidence else 0.0,
        "recency": evidence.recency if evidence else 0.0,
        "support_factor": evidence.support_factor if evidence else 0.0,
        "source_opportunities": evidence.source_opportunities if evidence else 0,
        "last_supported_at": evidence.last_supported_at if evidence else None,
        "median_lag_seconds": evidence.median_lag_seconds if evidence else None,
        "evidence_kind": evidence.evidence_kind if evidence else "",
        "observation": evidence.observation if evidence else "",
        "path_temporal_coverage": coverage,
        "available_segments": found,
        "total_segments": total,
        "temporal_path_evidence": [item.to_dict() for item in segments],
    }
    return body


def _candidate_row(
    node: GraphNode,
    ref: CanonicalFileRef | None,
    structural: dict[str, Any],
    temporal: dict[str, Any],
    origin: str,
    hybrid: float,
    alpha: float,
    beta: float,
    why: list[str],
) -> dict[str, Any]:
    return {
        "key": node.key,
        "service_id": node.service_id,
        "name": node.name,
        "qualified_name": node.qualified_name,
        "file_path": ref.relative_file_path if ref else (node.file_path or ""),
        "repository": ref.repository if ref else "",
        "category": node.category,
        "kind": node.kind,
        "line_start": node.line_start,
        "canonical_file": ref.token() if ref else None,
        "structural": structural,
        "temporal": temporal,
        "evidence_coverage": {
            "structural": structural["available"],
            "temporal": temporal["available"],
        },
        "hybrid_score": hybrid,
        "score_kind": HYBRID_SCORE_KIND,
        "evidence_origin": origin,
        "weights": {"structural": alpha, "temporal": beta},
        "why_ranked": why,
    }


def _why(item: AffectedNode, temporal: PairEvidence | None, origin: str, snapshot: Snapshot) -> list[str]:
    facts = []
    if item.path:
        step = item.path[-1]
        target = snapshot.nodes.get(step.target)
        relation = step.relation
        label = target.name if target else step.target
        facts.append(f"Reachable through {relation} {label}")
        contract = next((snapshot.nodes.get(step.target) for step in item.path if snapshot.nodes.get(step.target) and snapshot.nodes[step.target].category == "http_contract"), None)
        if contract is not None:
            facts.append(f"Reachable through HTTP contract {contract.name}")
    facts.append(f"Strongest structural path score: {item.score:.2f}")
    if temporal and temporal.available:
        facts.extend(_temporal_facts(temporal))
    if origin == "structural_only":
        facts.append("No temporal evidence for the file-backed path; temporal contribution is zero.")
    return facts


def _temporal_facts(evidence: PairEvidence) -> list[str]:
    facts = []
    if evidence.evidence_kind == "structure_conditioned_lagged_history":
        facts.append(evidence.observation)
        facts.append(
            f"Lagged support: {evidence.support} of {evidence.source_opportunities} source events "
            f"within the configured window."
        )
    elif evidence.source_opportunities:
        facts.append(
            f"Historical directional support: {evidence.support} of {evidence.source_opportunities} source changes"
        )
    if evidence.last_supported_at:
        stamp = parse_iso(evidence.last_supported_at)
        if stamp is not None:
            age = max(0, int((datetime.now(timezone.utc) - stamp).total_seconds() / 86400))
            facts.append(f"Most recent supporting event: {age} days ago")
    facts.append(f"Temporal ranking score: {evidence.score:.2f} (not a probability)")
    return facts


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
