#!/usr/bin/env python3
"""Deterministic Nami Boundary Gate demonstration on fixture copies.

Copies fixtures under output/boundary-demo. It does not modify fixtures/ or repos.txt.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from dep_intel.cluster_service import ClusterEngine
from dep_intel.config import PROJECT_ROOT, Settings
from dep_intel.review_engine import ReviewEngine
from dep_intel.store import write_json

ROOT = PROJECT_ROOT
CRG = os.environ.get("CRG_BIN") or str(ROOT / "server" / ".venv" / "bin" / "code-review-graph")
ORIGINAL = (ROOT / "fixtures/auth-service/src/server.js").read_text(encoding="utf-8")


def main() -> None:
    if not Path(CRG).is_file() and not shutil.which(CRG):
        raise SystemExit(f"code-review-graph is not available at {CRG}")
    work = ROOT / "output" / "boundary-demo"
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
    _git_init(services["auth-service"])
    (work / "repos.txt").write_text("auth-service\nuser-service\nreport-job\n", encoding="utf-8")
    manifest_body = json.loads((ROOT / "fixtures" / "cluster-manifest.json").read_text(encoding="utf-8"))
    for service in manifest_body["services"]:
        service["repository"] = service["id"]
    (work / "cluster-manifest.json").write_text(json.dumps(manifest_body, indent=2) + "\n", encoding="utf-8")
    (work / "nami-review.yaml").write_text(_policy(), encoding="utf-8")
    settings = Settings(root=work, repos_file=work / "repos.txt", output_dir=work / "out", github_token="", ghe_token="", cluster_manifest=work / "cluster-manifest.json")
    os.environ["CLUSTER_MANIFEST"] = str(work / "cluster-manifest.json")
    _temporal(settings)
    engine = ClusterEngine(settings)
    try:
        built = engine.refresh()
        reviewer = ReviewEngine(settings, cluster=engine)
        base = _sha(services["auth-service"])
        scenarios = {
            "A_safe_body": _scenario_a(reviewer, services["auth-service"]),
            "B_remove_endpoint": _scenario_b(reviewer, services["auth-service"]),
            "C_additive_route": _scenario_c(reviewer, services["auth-service"]),
            "D_test_gap": _scenario_d(reviewer, services["auth-service"]),
            "E_temporal_only": _scenario_e(reviewer, services["auth-service"]),
            "F_stale_graph": _scenario_f(reviewer, services["auth-service"], base),
        }
        report = {"refresh": built.get("status"), "graph_version": engine.current().version if engine.current() else "", "scenarios": scenarios}
        write_json(settings.output_dir, "boundary_gate_demo.json", report)
        print(json.dumps(report, indent=2))
    finally:
        engine.close()


def _scenario_a(reviewer: ReviewEngine, repo: Path) -> dict:
    _restore(repo)
    _write(repo, ORIGINAL.replace("{ ok: true }", "{ ok: true, edited: true }"))
    report = _run(reviewer, repo)
    return _compact(report, expect="body change is not a contract break")


def _scenario_b(reviewer: ReviewEngine, repo: Path) -> dict:
    _restore(repo)
    _write(repo, ORIGINAL.replace('app.post("/api/auth/validate", (req, res) => {\n  res.json({ ok: true });\n});\n\n', ""))
    report = _run(reviewer, repo)
    body = _compact(report, expect="removed consumed route blocks")
    body["contract_break"] = any(item.get("category") == "contract_break" for item in report.get("findings") or [])
    return body


def _scenario_c(reviewer: ReviewEngine, repo: Path) -> dict:
    _restore(repo)
    _write(repo, ORIGINAL.replace("app.listen(3001);", 'app.get("/api/auth/health", (req, res) => res.json({ ok: true }));\n\napp.listen(3001);'))
    report = _run(reviewer, repo)
    body = _compact(report, expect="added route is not a break")
    body["breaking"] = any(item.get("breaking") for item in report.get("contract_changes") or [])
    return body


def _scenario_d(reviewer: ReviewEngine, repo: Path) -> dict:
    _restore(repo)
    _write(repo, ORIGINAL.replace("return value;", "return value + 1;"))
    report = _run(reviewer, repo)
    body = _compact(report, expect="missing related test")
    body["test_gap"] = any(item.get("category") == "test_gap" for item in report.get("findings") or [])
    return body


def _scenario_e(reviewer: ReviewEngine, repo: Path) -> dict:
    _restore(repo)
    _write(repo, ORIGINAL.replace("function unusedHelper(value) {", "function unusedHelper(value) { // reviewed"))
    report = _run(reviewer, repo)
    temporal = [item for item in (report.get("impact") or {}).get("ranked_candidates") or [] if item.get("evidence_origin") == "temporal_only"]
    body = _compact(report, expect="temporal-only candidate is not a blocker")
    body["temporal_only"] = [item.get("file_path") for item in temporal]
    body["temporal_statement"] = temporal[0].get("statement") if temporal else ""
    return body


def _scenario_f(reviewer: ReviewEngine, repo: Path, base: str) -> dict:
    _restore(repo)
    _write(repo, ORIGINAL.replace("{ ok: true }", "{ ok: true, stale: true }"))
    _commit(repo, "later")
    head = _sha(repo)
    report = reviewer.run({
        "source": {"kind": "range", "repository": "auth-service", "base_ref": base, "head_ref": head},
        "service": "auth-service",
        "mode": "fast",
        "verify": False,
        "model": {"enabled": False},
        "config": {"policies": [], "verification": {"enabled": False, "commands": []}, "gate": {"strict_graph": True}},
    })
    body = _compact(report, expect="stale graph can be incomplete")
    body["alignment"] = (report.get("change") or {}).get("graph_alignment")
    return body


def _run(reviewer: ReviewEngine, repo: Path) -> dict:
    return reviewer.run({"source": {"kind": "working_tree", "repository": repo.name}, "service": "auth-service", "mode": "fast", "verify": False, "model": {"enabled": False}})


def _compact(report: dict, expect: str) -> dict:
    impact = report.get("impact") or {}
    candidates = impact.get("ranked_candidates") or []
    first = candidates[0] if candidates else {}
    blocking = [item for item in report.get("findings") or [] if item.get("finding_id") in set((report.get("gate") or {}).get("blocking_findings") or [])]
    return {
        "expect": expect,
        "decision": (report.get("gate") or {}).get("decision"),
        "reason_codes": (report.get("gate") or {}).get("reason_codes"),
        "changed": [item.get("new_path") or item.get("old_path") for item in report.get("changed_files") or []],
        "contracts": [f"{item.get('kind')} {item.get('method')} {item.get('path')}".strip() for item in report.get("contract_changes") or []],
        "affected_services": impact.get("affected_services"),
        "top_candidate": {"service": first.get("service_id"), "file": first.get("file_path"), "origin": first.get("evidence_origin"), "statement": first.get("statement"), "hybrid_score": first.get("hybrid_score")},
        "consumers": [
            f"{item.get('service_id')}:{item.get('qualified_name')}"
            for contract in report.get("contract_changes") or []
            for item in contract.get("consumers") or []
        ],
        "blocking": [item.get("category") for item in blocking],
        "review_id": report.get("review_id"),
    }


def _policy() -> str:
    return """schema_version: 1
gate:
  block_on_breaking_contract_with_known_consumers: true
  strict_graph: false
verification:
  enabled: false
  commands: []
policies:
  - id: api-removal-review
    description: Removing a consumed API requires compatibility review.
    severity: high
    blocking: true
    check:
      type: contract_compatibility
  - id: auth-tests
    description: Source changes require a related test.
    severity: high
    scope:
      paths: ["src/**"]
      file_classes: ["source"]
    check:
      type: required_related_test
"""


def _temporal(settings: Settings) -> None:
    directory = settings.output_dir / "graphs" / "auth_service"
    directory.mkdir(parents=True)
    write_json(directory, "temporal_evidence.json", {
        "schema_version": 1,
        "slug": "auth_service",
        "history": {"complete": True, "sampled": False},
        "events": [{"sha": "old", "committed_at": "2024-01-01T00:00:00+00:00", "files": ["src/server.js", "package.json"]}],
        "note": "Directional same-repository history used by the temporal-only demonstration.",
    })


def _build(path: Path) -> None:
    completed = subprocess.run([CRG, "build", "--repo", str(path), "--skip-postprocess", "-q"], capture_output=True, text=True, timeout=120, check=False)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr[-500:] or completed.stdout[-500:] or "code-review-graph build failed")


def _git_init(repo: Path) -> None:
    _git(repo, "init", "-b", "main")
    _git(repo, "add", "-A")
    _commit(repo, "base")


def _restore(repo: Path) -> None:
    _git(repo, "checkout", "--", "src/server.js")


def _write(repo: Path, text: str) -> None:
    (repo / "src" / "server.js").write_text(text, encoding="utf-8")


def _commit(repo: Path, message: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", message)


def _sha(repo: Path) -> str:
    completed = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True)
    return completed.stdout.strip()


def _git(repo: Path, *args: str) -> None:
    env = {**os.environ, "GIT_AUTHOR_NAME": "Nami", "GIT_AUTHOR_EMAIL": "nami@example.com", "GIT_COMMITTER_NAME": "Nami", "GIT_COMMITTER_EMAIL": "nami@example.com"}
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=env)


if __name__ == "__main__":
    main()
