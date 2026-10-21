#!/usr/bin/env python3
"""A/B/C ranking evaluation for Nami Rank Module (NRM).

Experiments:
    A = temporal_only
    B = structural_only
    C = hybrid

This script does not fabricate a research dataset. Pass a labeled JSON/JSONL
file. fixtures/hybrid-eval/ is a tiny synthetic demonstration only.
Ranking scores are not probabilities.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_VENV = ROOT / "server" / ".venv"
_VENV_PYTHON = _VENV / "bin" / "python"
if _VENV_PYTHON.is_file() and Path(sys.prefix).resolve() != _VENV.resolve():
    os.execv(str(_VENV_PYTHON), [str(_VENV_PYTHON), *sys.argv])
sys.path.insert(0, str(ROOT / "server"))

from dep_intel.cluster_manifest import ImpactSettings  # noqa: E402
from dep_intel.cross_repo_graph import index_snapshot  # noqa: E402
from dep_intel.graph_models import (  # noqa: E402
    CODE_SYMBOL,
    GraphEdge,
    GraphNode,
    HTTP_CONTRACT,
    Partition,
    symbol_key,
)
from dep_intel.hybrid_impact import HybridSettings, analyze_hybrid  # noqa: E402
from dep_intel.impact import analyze  # noqa: E402
from dep_intel.temporal_providers import MemoryTemporalProvider  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate temporal-only, structural-only, and Nami Rank Module (NRM) ranking.")
    parser.add_argument("dataset", nargs="?", default=str(ROOT / "fixtures" / "hybrid-eval" / "dataset.json"))
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--jsonl", default="", help="Optional ground-truth JSONL overlay.")
    args = parser.parse_args()
    dataset = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
    if args.jsonl:
        dataset["queries"] = [json.loads(line) for line in Path(args.jsonl).read_text(encoding="utf-8").splitlines() if line.strip()]
    snapshot = _snapshot(dataset)
    provider = MemoryTemporalProvider(dataset.get("temporal_events") or {}, dataset.get("service_to_slug") or {})
    k = args.k
    experiments = {
        "A_temporal_only": "temporal_only",
        "B_structural_only": "structural_only",
        "C_hybrid": "hybrid",
    }
    report = {"dataset": str(Path(args.dataset)), "k": k, "experiments": {}, "coverage": []}
    for label, experiment in experiments.items():
        metrics = []
        for query in dataset.get("queries") or []:
            metrics.append(_evaluate_query(snapshot, provider, query, experiment, k))
        report["experiments"][label] = _mean_metrics(metrics)
    for query in dataset.get("queries") or []:
        report["coverage"].append(_coverage(snapshot, provider, query))
    print(json.dumps(report, indent=2))


def _evaluate_query(snapshot, provider, query: dict, experiment: str, k: int) -> dict:
    structural = analyze(snapshot, [_root_key(snapshot, query)], mode=query.get("mode") or "code_change", settings=ImpactSettings())
    payload = analyze_hybrid(
        snapshot,
        structural,
        provider,
        settings=HybridSettings(
            expand_temporal=True,
            experiment=experiment,
            alpha_structural=float((query.get("weights") or {}).get("structural", 0.7)),
            beta_temporal=float((query.get("weights") or {}).get("temporal", 0.3)),
        ),
        as_of=query.get("as_of"),
        provider_name="memory",
    )
    ranked = [_identity(row) for row in payload["candidates"]]
    relevant = {_identity(item) for item in query.get("relevant") or []}
    hits = [1.0 if item in relevant else 0.0 for item in ranked]
    return {
        "precision_at_k": _precision_at_k(hits, relevant, k),
        "recall_at_k": _recall_at_k(hits, relevant, k),
        "f1_at_k": _f1(_precision_at_k(hits, relevant, k), _recall_at_k(hits, relevant, k)),
        "map": _average_precision(hits, relevant),
        "mrr": _mrr(hits),
        "ndcg_at_k": _ndcg(hits, k, len(relevant)),
    }


def _coverage(snapshot, provider, query: dict) -> dict:
    structural = analyze(snapshot, [_root_key(snapshot, query)], mode=query.get("mode") or "code_change", settings=ImpactSettings())
    hybrid = analyze_hybrid(
        snapshot,
        structural,
        provider,
        settings=HybridSettings(expand_temporal=True, experiment="hybrid"),
        as_of=query.get("as_of"),
        provider_name="memory",
    )
    relevant = {_identity(item) for item in query.get("relevant") or []}
    structural_ids = {_identity(row) for row in hybrid["candidates"] if row["structural"]["available"]}
    temporal_ids = {_identity(row) for row in hybrid["candidates"] if row["temporal"]["available"]}
    union = structural_ids | temporal_ids
    return {
        "query": query.get("id") or query.get("name"),
        "ground_truth": len(relevant),
        "structurally_reachable": len(relevant & structural_ids),
        "temporally_reachable": len(relevant & temporal_ids),
        "union_reachable": len(relevant & union),
        "generation_misses": sorted(relevant - union),
    }


def _snapshot(dataset: dict):
    nodes = []
    for item in dataset.get("nodes") or []:
        service = item["service"]
        name = item["name"]
        file = item.get("file") or ""
        category = item.get("category") or CODE_SYMBOL
        kind = item.get("kind") or ("http_contract" if category == HTTP_CONTRACT else "Function")
        qualified = item.get("qualified") or (f"{file}::{name}" if file else name)
        nodes.append(
            GraphNode(
                key=item.get("key") or symbol_key(service, file, qualified, kind),
                category=category,
                service_id=service,
                name=name,
                qualified_name=qualified,
                file_path=file,
                line_start=item.get("line") or 1,
                kind=kind,
            )
        )
    by_name = {(node.service_id, node.name): node for node in nodes}
    edges = []
    for item in dataset.get("edges") or []:
        origin = by_name[(item["source_service"], item["source"])]
        target = by_name[(item["target_service"], item["target"])]
        edges.append(GraphEdge(origin.key, target.key, item["relation"], {"status": "extracted", "source": "crg", "confidence": 1.0}))
    partitions: dict[str, Partition] = {}
    for node in nodes:
        part = partitions.setdefault(node.service_id, Partition(service_id=node.service_id, repository=node.service_id))
        part.nodes[node.key] = node
    return index_snapshot(
        version="eval",
        generated_at="2026-01-01T00:00:00+00:00",
        partitions=partitions,
        overlay={},
        bridges=edges,
        diagnostics=[],
        fingerprints=[],
    )


def _root_key(snapshot, query: dict) -> str:
    service = query["root"]["service"]
    name = query["root"]["name"]
    for node in snapshot.nodes.values():
        if node.service_id == service and node.name == name:
            return node.key
    raise KeyError(f"Unknown root {service}:{name}")


def _identity(item: dict) -> tuple[str, str, str]:
    return (item.get("service") or item.get("service_id") or "", item.get("file") or item.get("file_path") or "", item.get("name") or "")


def _precision_at_k(hits: list[float], relevant: set, k: int) -> float:
    if k <= 0:
        return 0.0
    return sum(hits[:k]) / k


def _recall_at_k(hits: list[float], relevant: set, k: int) -> float:
    if not relevant:
        return 0.0
    return sum(hits[:k]) / len(relevant)


def _f1(precision: float, recall: float) -> float:
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def _average_precision(hits: list[float], relevant: set) -> float:
    if not relevant:
        return 0.0
    found = 0.0
    total = 0.0
    for index, hit in enumerate(hits, start=1):
        if not hit:
            continue
        found += 1
        total += found / index
    return total / len(relevant)


def _mrr(hits: list[float]) -> float:
    for index, hit in enumerate(hits, start=1):
        if hit:
            return 1.0 / index
    return 0.0


def _ndcg(hits: list[float], k: int, relevant_count: int) -> float:
    dcg = sum(hit / math.log2(index + 1) for index, hit in enumerate(hits[:k], start=1))
    ideal_hits = [1.0] * min(k, relevant_count)
    idcg = sum(hit / math.log2(index + 1) for index, hit in enumerate(ideal_hits, start=1))
    return (dcg / idcg) if idcg else 0.0


def _mean_metrics(rows: list[dict]) -> dict:
    if not rows:
        return {}
    keys = rows[0].keys()
    return {key: round(sum(row[key] for row in rows) / len(rows), 4) for key in keys}


if __name__ == "__main__":
    main()
