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


def analyze_hybrid(
    snapshot: Snapshot,
    structural: ImpactResult,
    provider: TemporalEvidenceProvider,
    *,
    settings: HybridSettings | None = None,
    as_of: str | None = None,
    provider_name: str = "commit_history",
) -> dict[str, Any]:
    options = settings or HybridSettings()
    alpha = _clamp(options.alpha_structural)
    beta = _clamp(options.beta_temporal)
    candidates: dict[str, dict[str, Any]] = {}
    for item in structural.affected:
        node = snapshot.nodes.get(item.key)
        if node is None:
            continue
        if item.path:
            root_key = item.path[0].source
        elif item.key in structural.roots:
            root_key = item.key
        else:
            root_key = structural.roots[0] if structural.roots else item.key
        chain = file_backed_chain(snapshot, root_key, item.path)
        segments = _segment_evidence(chain, provider, as_of, options)
        available_scores = [segment.score for segment in segments if segment.available]
        temporal_score = sum(available_scores) / len(available_scores) if available_scores else 0.0
        temporal_available = bool(available_scores)
        last = next((segment for segment in reversed(segments) if segment.available), None)
        structural_payload = _structural_payload(item)
        temporal_payload = _temporal_payload(last, temporal_score, temporal_available, segments)
        origin = "both" if temporal_available else "structural_only"
        hybrid = _hybrid_score(alpha, beta, item.score, temporal_score, True, temporal_available, options.experiment)
        ref = canonical_file_ref(node, snapshot)
        candidates[item.key] = _candidate_row(
            node,
            ref,
            structural_payload,
            temporal_payload,
            origin,
            hybrid,
            alpha,
            beta,
            _why(item, last, origin, snapshot),
        )

    if options.expand_temporal and options.experiment != "structural_only":
        for root_key in structural.roots:
            root = snapshot.nodes.get(root_key)
            if root is None:
                continue
            root_ref = canonical_file_ref(root, snapshot)
            if root_ref is None:
                continue
            for neighbour in provider.neighbours(root_ref, as_of, options.temporal_candidate_limit):
                for node in lookup_file_nodes(snapshot, neighbour.target):
                    if node.key in candidates:
                        current = candidates[node.key]
                        if not current["temporal"]["available"]:
                            current["temporal"] = _temporal_payload(neighbour, neighbour.score, True, [neighbour])
                            current["evidence_coverage"]["temporal"] = True
                            current["evidence_origin"] = "both"
                            current["hybrid_score"] = _hybrid_score(
                                alpha,
                                beta,
                                current["structural"]["score"],
                                neighbour.score,
                                current["structural"]["available"],
                                True,
                                options.experiment,
                            )
                            current["why_ranked"].extend(_temporal_facts(neighbour))
                        continue
                    structural_payload = {
                        "available": False,
                        "score": 0.0,
                        "hops": 0,
                        "path": [],
                        "score_kind": SCORE_KIND,
                    }
                    temporal_payload = _temporal_payload(neighbour, neighbour.score, True, [neighbour])
                    hybrid = _hybrid_score(alpha, beta, 0.0, neighbour.score, False, True, options.experiment)
                    candidates[node.key] = _candidate_row(
                        node,
                        neighbour.target,
                        structural_payload,
                        temporal_payload,
                        "temporal_only",
                        hybrid,
                        alpha,
                        beta,
                        [
                            "Included from historical neighbours; not structurally reachable in this snapshot.",
                            *_temporal_facts(neighbour),
                        ],
                    )

    rows = list(candidates.values())
    if options.experiment == "temporal_only":
        rows = [row for row in rows if row["temporal"]["available"]]
    rows.sort(
        key=lambda row: (
            -row["hybrid_score"],
            -row["structural"]["score"],
            -row["temporal"]["score"],
            row["service_id"],
            row["file_path"] or "",
            row["key"],
        )
    )
    diagnostics = [item.to_dict() for item in structural.diagnostics]
    if as_of:
        diagnostics.append(
            {
                "code": "temporal_cutoff",
                "message": f"Temporal evidence uses history at or before {as_of}. Later commits are excluded.",
                "severity": "info",
            }
        )
    return {
        "analysis_id": str(uuid.uuid4()),
        "structural_analysis_id": structural.analysis_id,
        "graph_version": structural.graph_version,
        "mode": structural.mode,
        "roots": list(structural.roots),
        "weights": {
            "structural": alpha,
            "temporal": beta,
            "research_defaults": alpha == 0.70 and beta == 0.30,
            "note": "Default research parameters. Not claimed to be optimal.",
        },
        "temporal_provider": provider_name,
        "temporal_cutoff": as_of,
        "experiment": options.experiment,
        "module": MODULE_NAME,
        "module_abbrev": MODULE_ABBREV,
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
            )
        )
    return segments


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
