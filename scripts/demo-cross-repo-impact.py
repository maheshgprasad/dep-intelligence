#!/usr/bin/env python3
"""Run the fixture cross-repository impact demonstration.

Uses temporary copies under output/demo. It does not modify fixtures/ or repos.txt.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from dep_intel.cluster_service import ClusterEngine
from dep_intel.config import PROJECT_ROOT, Settings
from dep_intel.crg_adapter import crg_package_version
from dep_intel.events import bus
from dep_intel.impact import LIMITATIONS

ROOT = PROJECT_ROOT
CRG = os.environ.get("CRG_BIN") or str(ROOT / "server" / ".venv" / "bin" / "code-review-graph")


def main() -> None:
    if not Path(CRG).is_file() and not shutil.which(CRG):
        raise SystemExit(f"code-review-graph is not available at {CRG}")
    work = ROOT / "output" / "demo"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    services = {}
    for name in ("auth-service", "user-service", "report-job"):
        dest = work / name
        shutil.copytree(ROOT / "fixtures" / name, dest, ignore=shutil.ignore_patterns(".code-review-graph"))
        services[name] = dest
        if name != "report-job":
            _build(dest)
    repos = work / "repos.txt"
    repos.write_text("auth-service\nuser-service\nreport-job\n", encoding="utf-8")
    manifest_body = json.loads((ROOT / "cluster-manifest.json").read_text(encoding="utf-8"))
    for service in manifest_body["services"]:
        service["repository"] = service["id"]
    manifest_body["watch"]["debounce_ms"] = 100
    manifest = work / "cluster-manifest.json"
    manifest.write_text(json.dumps(manifest_body, indent=2) + "\n", encoding="utf-8")
    settings = Settings(
        root=work,
        repos_file=repos,
        output_dir=work / "out",
        github_token="",
        ghe_token="",
        cluster_manifest=manifest,
    )
    os.environ["CLUSTER_MANIFEST"] = str(manifest)
    engine = ClusterEngine(settings)
    try:
        built = engine.refresh()
        snapshot = engine.current()
        assert snapshot is not None
        initial_summary = snapshot.summary()
        handler = next(node for node in snapshot.nodes.values() if node.category == "handler_anchor" and "validate" in node.qualified_name)
        status, impact = engine.impact({"service": "auth-service", "symbol": handler.key, "mode": "code_change", "graph_version": snapshot.version})
        before = snapshot.version
        events_before = len(bus._replay)
        engine.start_watcher()
        server = (services["auth-service"] / "src" / "server.js").read_text(encoding="utf-8")
        (services["auth-service"] / "src" / "server.js").write_text(server.replace("{ ok: true }", "{ ok: true, edited: true }"), encoding="utf-8")
        _build(services["auth-service"])
        deadline = time.time() + 8
        while time.time() < deadline:
            current = engine.current()
            if current and current.version != before:
                break
            time.sleep(0.1)
        watched = engine.current()
        if engine._watcher is not None:
            engine._watcher.stop()
        removed = server.replace('app.post("/api/auth/validate", (req, res) => {\n  res.json({ ok: true });\n});\n\n', "")
        (services["auth-service"] / "src" / "server.js").write_text(removed, encoding="utf-8")
        _build(services["auth-service"])
        engine.refresh(["auth-service"])
        removed_impact_status, removed_impact = engine.impact(
            {
                "service": "auth-service",
                "symbol": handler.key,
                "mode": "code_change",
                "topology": "previous",
            }
        )
        events = [item["event"] for item in list(bus._replay)[events_before:]]
        report = {
            "meta": {
                "generated_at": initial_summary.get("generated_at"),
                "crg_package": crg_package_version(),
                "schema_versions": sorted({part.schema_version for part in snapshot.partitions.values()}),
                "refresh_status": built.get("status"),
            },
            "repositories": 3,
            "symbols": initial_summary.get("node_count"),
            "edges": initial_summary.get("edge_count"),
            "contracts": initial_summary.get("contract_count"),
            "unresolved": initial_summary.get("unresolved_count"),
            "diagnostics": len(initial_summary.get("diagnostics") or []),
            "handler": {"category": handler.category, "file": handler.file_path, "line": handler.line_start, "exact_crg_symbol": False},
            "impact_status": status,
            "affected": [
                {"name": item["name"], "service_id": item["service_id"], "structural_score": item["structural_score"], "file": item["file_path"], "line": item["line_start"]}
                for item in impact.get("affected", [])
            ],
            "watcher_revision_changed": bool(watched and watched.version != before),
            "watcher_version": watched.version if watched else "",
            "events": events,
            "removed_route_status": removed_impact_status,
            "removed_route_still_finds_list_users": any(item["name"] == "listUsers" for item in removed_impact.get("affected", [])),
            "limitations": LIMITATIONS,
        }
        destination = work / "out" / "demo_result.json"
        destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2))
    finally:
        engine.close()


def _build(path: Path) -> None:
    completed = subprocess.run([CRG, "build", "--repo", str(path), "--skip-postprocess", "-q"], capture_output=True, text=True, timeout=120, check=False)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr[-500:] or completed.stdout[-500:] or "code-review-graph build failed")


if __name__ == "__main__":
    main()
