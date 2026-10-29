#!/usr/bin/env python3
"""Show the evidence chain for a consumed auth-route removal.

Runs the existing review engine on an in-memory copy of the auth fixture.
Writes under output/demo-evidence-gate. Does not edit fixtures or repos.txt.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

from dep_intel.config import Settings  # noqa: E402
from dep_intel.cross_repo_graph import index_snapshot  # noqa: E402
from dep_intel.graph_models import CONSUMES_HTTP, HTTP_CONTRACT, GraphEdge, GraphNode, Partition, symbol_key  # noqa: E402
from dep_intel.review_engine import ReviewEngine  # noqa: E402
from dep_intel.review_export import ensure_export  # noqa: E402


def main() -> int:
    workspace = ROOT / "output" / "demo-evidence-gate"
    repo = workspace / "repo"
    (repo / "src").mkdir(parents=True, exist_ok=True)
    output = workspace / "output"
    output.mkdir(parents=True, exist_ok=True)
    (workspace / "repos.txt").write_text("repo\n", encoding="utf-8")
    fixture = (ROOT / "fixtures/auth-service/src/server.js").read_text(encoding="utf-8")
    removed = fixture.replace('app.post("/api/auth/validate", (req, res) => {\n  res.json({ ok: true });\n});\n\n', "")
    (repo / "src" / "server.js").write_text(removed, encoding="utf-8")
    settings = Settings(
        root=workspace,
        repos_file=workspace / "repos.txt",
        output_dir=output,
        github_token="",
        ghe_token="",
    )
    engine = ReviewEngine(settings, cluster=_Cluster(_snapshot(), [_candidate()]))
    report = engine.run({
        "source": {
            "kind": "explicit",
            "repository": "repo",
            "service_id": "auth-service",
            "base_ref": "basesha",
            "head_ref": "headsha",
            "files": [{"path": "src/server.js", "old_text": fixture, "new_text": removed, "change_type": "modified"}],
        },
        "service": "auth-service",
        "mode": "fast",
        "verify": False,
        "model": {"enabled": False},
        "config": {"policies": [], "verification": {"enabled": False, "commands": []}, "gate": {"strict_graph": False}},
    })
    pdf = ensure_export(output, report["review_id"], "pdf")
    package = ensure_export(output, report["review_id"], "zip")
    rule = next(item for item in report["gate"]["decision_rules"] if item["rule_id"] == "BG-CONTRACT-001")
    inference = next(item for item in report["inferences"] if item["type"] == "consumer_compatibility_break")
    print(f"Decision: {report['gate']['decision']}")
    print(f"Assessment: {report['assessment']['headline']}")
    print(f"Inference: {inference['inference_id']} {inference['type']}")
    print(f"Rule: {rule['rule_id']} {rule['title']}")
    print(f"Evidence: {', '.join(rule['evidence_refs'])}")
    for action in report["actions"]:
        if action["required"]:
            print(f"Required: {action['title']}")
    print(f"Enforcement: {report['enforcement']['enforcement_mode']} workflow_prevented={report['enforcement']['workflow_prevented']}")
    print(f"PDF: {pdf}")
    print(f"Package: {package}")
    return 0 if report["gate"]["decision"] == "block" else 1


def _candidate() -> dict:
    return {
        "service_id": "user-service",
        "repository": "user-service",
        "file_path": "src/users.js",
        "name": "listUsers",
        "hybrid_score": 0.9,
        "evidence_origin": "structural_only",
        "structural": {"score": 0.8, "available": True, "path": []},
        "temporal": {"score": 0.0, "available": False},
    }


class _Cluster:
    def __init__(self, snapshot, candidates: list[dict]) -> None:
        self.snapshot = snapshot
        self.candidates = candidates

    def current(self):
        return self.snapshot

    def previous(self):
        return None

    def hybrid_impact(self, _body):
        return 200, {"graph_version": self.snapshot.version, "candidates": self.candidates, "diagnostics": []}


def _snapshot():
    identity = "http::POST:/api/auth/validate"
    contract = GraphNode(
        key=symbol_key("auth-service", "", identity, HTTP_CONTRACT),
        category=HTTP_CONTRACT,
        service_id="auth-service",
        name="POST /api/auth/validate",
        qualified_name=identity,
        file_path="",
        kind=HTTP_CONTRACT,
        metadata={"identity": identity},
    )
    caller = GraphNode(
        key=symbol_key("user-service", "src/users.js", "src/users.js::listUsers", "Function"),
        category="code_symbol",
        service_id="user-service",
        name="listUsers",
        qualified_name="src/users.js::listUsers",
        file_path="src/users.js",
        kind="Function",
    )
    auth = Partition(service_id="auth-service", repository="repo", workspace_head="headsha")
    user = Partition(service_id="user-service", repository="user-service", workspace_head="headsha")
    auth.nodes[contract.key] = contract
    user.nodes[caller.key] = caller
    edge = GraphEdge(caller.key, contract.key, CONSUMES_HTTP, {"status": "extracted"})
    return index_snapshot(
        version="g",
        generated_at="2026-01-01T00:00:00+00:00",
        partitions={"auth-service": auth, "user-service": user},
        overlay={},
        bridges=[edge],
        diagnostics=[],
        fingerprints=[],
    )


if __name__ == "__main__":
    raise SystemExit(main())
