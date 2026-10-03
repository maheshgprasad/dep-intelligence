#!/usr/bin/env python3
"""Synthetic structural-impact benchmark. Timing is reported, not asserted."""

from __future__ import annotations

import json
import platform
import random
import sqlite3
import time
import tracemalloc
from pathlib import Path

from dep_intel.cluster_manifest import ImpactSettings
from dep_intel.crg_adapter import extract_partition
from dep_intel.cross_repo_graph import index_snapshot
from dep_intel.graph_models import CODE_SYMBOL, CONSUMES_HTTP, HANDLED_BY, HTTP_CONTRACT, GraphEdge, GraphNode, Partition, symbol_key
from dep_intel.impact import analyze

NODES = 10000
EDGES = 50000
SERVICES = 4
ROOTS = 40


def main() -> None:
    random.seed(13)
    output = Path(__file__).resolve().parents[1] / "output"
    output.mkdir(parents=True, exist_ok=True)
    root = output / "benchmark-repos"
    if root.exists():
        import shutil

        shutil.rmtree(root)
    databases = []
    per_service = NODES // SERVICES
    tracemalloc.start()
    started = time.perf_counter()
    for service_index in range(SERVICES):
        repo = root / f"service-{service_index}"
        db = repo / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        _write_db(db, repo, service_index, per_service)
        databases.append((service_index, repo, db))
    write_seconds = time.perf_counter() - started
    extract_started = time.perf_counter()
    partitions = {}
    for service_index, repo, db in databases:
        partitions[f"s{service_index}"] = extract_partition(
            db, service_id=f"s{service_index}", repository=repo.name, repo_root=repo, package_version="benchmark"
        )
    extract_seconds = time.perf_counter() - started - write_seconds
    nodes = []
    bridges = []
    for part in partitions.values():
        nodes.extend(part.nodes.values())
    _add_borders(nodes, bridges)
    snapshot = index_snapshot(
        version="bench",
        generated_at="",
        partitions=partitions,
        overlay={node.key: node for node in nodes if node.category == HTTP_CONTRACT},
        bridges=bridges,
        diagnostics=[],
        fingerprints=[],
    )
    cold_seconds = time.perf_counter() - started
    incremental_started = time.perf_counter()
    connection = sqlite3.connect(databases[0][2])
    connection.execute("UPDATE nodes SET line_end = line_end + 1 WHERE id = 1")
    connection.commit()
    connection.close()
    partitions["s0"] = extract_partition(
        databases[0][2], service_id="s0", repository="service-0", repo_root=databases[0][1], package_version="benchmark"
    )
    index_snapshot(version="bench-2", generated_at="", partitions=partitions, overlay={}, bridges=bridges, diagnostics=[], fingerprints=[])
    incremental_seconds = time.perf_counter() - incremental_started
    roots = [node.key for node in list(snapshot.nodes.values()) if node.category == CODE_SYMBOL][:ROOTS]
    settings = ImpactSettings(timeout_ms=5000, max_nodes=10000, max_edges_examined=200000, max_hops=32)
    samples = []
    for key in roots:
        sample = time.perf_counter()
        analyze(snapshot, [key], mode="code_change", settings=settings)
        samples.append((time.perf_counter() - sample) * 1000)
    samples.sort()
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    report = {
        "hardware": {"platform": platform.platform(), "python": platform.python_version(), "machine": platform.machine()},
        "graph": {"symbols": len(snapshot.nodes), "edges": len(snapshot.edges), "services": SERVICES},
        "seconds": {
            "sqlite_write": round(write_seconds, 3),
            "sqlite_extract": round(extract_seconds, 3),
            "cold_build": round(cold_seconds, 3),
            "incremental_one_service": round(incremental_seconds, 3),
        },
        "traversal_ms": {
            "roots": len(samples),
            "p50": round(samples[len(samples) // 2], 3) if samples else None,
            "p95": round(samples[max(0, int(len(samples) * 0.95) - 1)], 3) if samples else None,
        },
        "memory_mib": {"tracemalloc_peak": round(peak / (1024 * 1024), 2), "rss": round(_rss_mib(), 2)},
        "targets": {"warm_traversal_p95_ms": 500, "process_memory_mib": 250},
        "note": "Watcher debounce is not end-to-end latency. Extraction time and traversal time are separate.",
    }
    destination = output / "benchmark_impact.json"
    destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


def _write_db(path: Path, repo: Path, service_index: int, count: int) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE nodes (
            id INTEGER PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL, qualified_name TEXT NOT NULL UNIQUE,
            file_path TEXT NOT NULL, line_start INTEGER, line_end INTEGER, language TEXT, is_test INTEGER DEFAULT 0,
            file_hash TEXT, extra TEXT DEFAULT '{}', updated_at REAL NOT NULL
        );
        CREATE TABLE edges (
            id INTEGER PRIMARY KEY, kind TEXT NOT NULL, source_qualified TEXT NOT NULL, target_qualified TEXT NOT NULL,
            file_path TEXT NOT NULL, line INTEGER, extra TEXT DEFAULT '{}', confidence REAL, confidence_tier TEXT,
            target_resolution TEXT, updated_at REAL NOT NULL
        );
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """
    )
    connection.execute("INSERT INTO metadata VALUES ('schema_version', '13')")
    connection.execute("INSERT INTO metadata VALUES ('last_updated', 'benchmark')")
    file_path = str(repo / "src" / "mod.js")
    nodes = []
    for index in range(count):
        name = f"fn{index}"
        qualified = f"{file_path}::{name}"
        nodes.append((index + 1, "Function", name, qualified, file_path, index + 1, index + 2, "javascript", "{}", 1))
    connection.executemany(
        "INSERT INTO nodes (id, kind, name, qualified_name, file_path, line_start, line_end, language, extra, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        nodes,
    )
    edges = []
    edge_id = 1
    per_node = max(1, (EDGES // SERVICES) // count)
    for index in range(count):
        source = f"{file_path}::fn{index}"
        for hop in range(per_node):
            target_index = (index + hop + 1) % count
            kind = "CALLS" if hop else "REFERENCES"
            edges.append((edge_id, kind, source, f"{file_path}::fn{target_index}", file_path, index + 1, "{}", 1.0, "EXTRACTED", "direct", 1))
            edge_id += 1
            if len(edges) >= EDGES // SERVICES:
                break
        if len(edges) >= EDGES // SERVICES:
            break
    connection.executemany(
        "INSERT INTO edges (id, kind, source_qualified, target_qualified, file_path, line, extra, confidence, confidence_tier, target_resolution, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        edges,
    )
    connection.commit()
    connection.close()


def _add_borders(nodes: list[GraphNode], bridges: list[GraphEdge]) -> None:
    symbols = [node for node in nodes if node.category == CODE_SYMBOL]
    for index in range(0, min(len(symbols), 200), 2):
        provider = symbols[index]
        contract = GraphNode(
            key=symbol_key(provider.service_id, "", f"http::{index}", HTTP_CONTRACT),
            category=HTTP_CONTRACT,
            service_id=provider.service_id,
            name=f"POST /api/{index}",
            qualified_name=f"http::{index}",
            file_path="",
            kind=HTTP_CONTRACT,
            metadata={"identity": f"http::{index}"},
        )
        nodes.append(contract)
        bridges.append(GraphEdge(contract.key, provider.key, HANDLED_BY, {"status": "extracted", "confidence": 1.0, "injected": True}))
        if index + 1 < len(symbols):
            bridges.append(GraphEdge(symbols[index + 1].key, contract.key, CONSUMES_HTTP, {"status": "extracted", "confidence": 1.0, "injected": True}))


def _rss_mib() -> float:
    try:
        for line in Path("/proc/self/status").read_text(encoding="utf-8").splitlines():
            if line.startswith("VmHWM:"):
                return int(line.split()[1]) / 1024
    except OSError:
        return 0
    return 0


if __name__ == "__main__":
    main()
