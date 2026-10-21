#!/usr/bin/env python3
"""Print one Nami Rank Module (NRM) ranking for the fixture auth -> user HTTP path.

Does not modify fixtures/ or repos.txt. Scores are ranking heuristics, not
probabilities.
"""

from __future__ import annotations

import json
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
    CONSUMES_HTTP,
    GraphEdge,
    GraphNode,
    HANDLED_BY,
    HTTP_CONTRACT,
    Partition,
    symbol_key,
)
from dep_intel.hybrid_impact import HybridSettings, analyze_hybrid  # noqa: E402
from dep_intel.impact import analyze  # noqa: E402
from dep_intel.temporal_providers import MemoryTemporalProvider  # noqa: E402


def _node(service: str, name: str, file: str, category: str = CODE_SYMBOL, kind: str = "Function") -> GraphNode:
    qualified = f"{file}::{name}" if file else name
    return GraphNode(
        key=symbol_key(service, file, qualified, kind),
        category=category,
        service_id=service,
        name=name,
        qualified_name=qualified,
        file_path=file,
        line_start=1,
        kind=kind,
    )


def main() -> None:
    handler = _node("auth-service", "validate", "src/server.js")
    contract = GraphNode(
        key=symbol_key("auth-service", "", "http::POST:/api/auth/validate", HTTP_CONTRACT),
        category=HTTP_CONTRACT,
        service_id="auth-service",
        name="POST /api/auth/validate",
        qualified_name="http::POST:/api/auth/validate",
        file_path="",
        kind=HTTP_CONTRACT,
    )
    client = _node("user-service", "listUsers", "src/users.js")
    partitions: dict[str, Partition] = {}
    for node in (handler, contract, client):
        part = partitions.setdefault(node.service_id, Partition(service_id=node.service_id, repository=node.service_id))
        part.nodes[node.key] = node
    snapshot = index_snapshot(
        version="demo",
        generated_at="2026-01-01T00:00:00+00:00",
        partitions=partitions,
        overlay={},
        bridges=[
            GraphEdge(contract.key, handler.key, HANDLED_BY, {"status": "extracted", "source": "extractor", "confidence": 1.0}),
            GraphEdge(client.key, contract.key, CONSUMES_HTTP, {"status": "extracted", "source": "extractor", "confidence": 1.0}),
        ],
        diagnostics=[],
        fingerprints=[],
    )
    structural = analyze(snapshot, [handler.key], mode="code_change", settings=ImpactSettings())
    provider = MemoryTemporalProvider(
        {
            "auth-service": [{"committed_at": "2026-01-01T00:00:00+00:00", "files": ["src/server.js"]}],
            "user-service": [{"committed_at": "2026-01-04T00:00:00+00:00", "files": ["src/users.js"]}],
        },
        {"auth-service": "auth-service", "user-service": "user-service"},
    )
    payload = analyze_hybrid(
        snapshot,
        structural,
        provider,
        settings=HybridSettings(expand_temporal=False),
        as_of="2026-01-22T00:00:00+00:00",
        provider_name="memory",
    )
    print(json.dumps(payload, indent=2, default=str))


if __name__ == "__main__":
    main()
