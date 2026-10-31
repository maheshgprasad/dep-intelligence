"""Boundary Gate engine, causal cutoff, and feedback isolation."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dep_intel.cluster_manifest import ImpactSettings
from dep_intel.config import Settings
from dep_intel.evidence_bridge import CanonicalFileRef
from dep_intel.graph_models import CODE_SYMBOL, GraphNode, symbol_key
from dep_intel.hybrid_impact import analyze_hybrid
from dep_intel.impact import analyze
from dep_intel.review_engine import ReviewEngine
from dep_intel.review_feedback import record_feedback
from dep_intel.temporal_providers import MemoryTemporalProvider
from dep_intel.cluster_service import resolve_roots
from dep_intel.cross_repo_graph import index_snapshot
from dep_intel.graph_models import Partition


def test_placeholder_secret_blocks_without_a_model(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "src").mkdir()
    (repo / "src" / "app.js").write_text("function ok() { return 1 }\n", encoding="utf-8")
    _commit(repo, "base")
    (repo / "src" / "app.js").write_text("function ok() { return 'PLACEHOLDER_SECRET_VALUE' }\n", encoding="utf-8")
    engine = ReviewEngine(_settings(tmp_path), cluster=None)
    report = engine.run({"source": {"kind": "working_tree", "repository": "repo"}, "verify": False, "model": {"enabled": False}, "config": _secret_config()})
    assert report["gate"]["decision"] == "block"
    assert any(item["category"] == "security_risk" for item in report["findings"])
    stored = json.loads((tmp_path / "output" / "reviews" / report["review_id"] / "report.json").read_text(encoding="utf-8"))
    assert stored["module_abbrev"] == "NBG"
    assert "PLACEHOLDER" not in json.dumps(stored.get("performance"))


def test_future_history_is_not_visible_at_the_review_cutoff(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "src").mkdir()
    (repo / "src" / "a.js").write_text("function a() {}\n", encoding="utf-8")
    _commit(repo, "t1", when="2024-01-01T00:00:00+00:00")
    (repo / "src" / "a.js").write_text("function a() { return 1 }\n", encoding="utf-8")
    _commit(repo, "t2", when="2024-06-01T00:00:00+00:00")
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    base = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD^"], check=True, capture_output=True, text=True).stdout.strip()
    source = _node("auth", "a", "src/a.js")
    later = _node("auth", "c", "src/c.js")
    snapshot = _snap([source, later])
    t1 = "2024-01-01T00:00:00+00:00"
    t3 = "2024-12-01T00:00:00+00:00"
    provider = MemoryTemporalProvider({"auth": [
        {"sha": "1", "committed_at": t1, "files": ["src/a.js", "src/b.js"]},
        {"sha": "3", "committed_at": t3, "files": ["src/a.js", "src/c.js"]},
    ]}, {"auth": "auth"})
    cutoff = "2024-06-01T00:00:00+00:00"

    class CutoffCluster:
        def __init__(self) -> None:
            self.as_of = None

        def current(self):
            return snapshot

        def previous(self):
            return None

        def hybrid_impact(self, body):
            self.as_of = body.get("as_of")
            roots, _candidates, error = resolve_roots(snapshot, service="auth", symbol=source.key, file="", line=None, contract_id="")
            assert not error
            structural = analyze(snapshot, roots, mode="code_change", settings=ImpactSettings())
            return 200, analyze_hybrid(snapshot, structural, provider, as_of=body.get("as_of"))

    cluster = CutoffCluster()
    report = ReviewEngine(_settings(tmp_path), cluster=cluster).run({
        "source": {"kind": "range", "repository": "repo", "base_ref": base, "head_ref": head},
        "service": "auth",
        "verify": False,
        "model": {"enabled": False},
        "config": {"policies": [], "verification": {"enabled": False, "commands": []}},
    })
    assert cluster.as_of is not None
    assert datetime.fromisoformat(cluster.as_of) == datetime.fromisoformat(cutoff)
    paths = [item.get("file_path") for item in report["impact"]["ranked_candidates"]]
    assert "src/c.js" not in paths
    ref = CanonicalFileRef("auth", "auth", "src/a.js")
    assert all(item.target.relative_file_path != "src/c.js" for item in provider.neighbours(ref, cutoff, 10))
    assert datetime.fromisoformat(t3) > datetime.fromisoformat(cutoff)
    assert datetime.fromisoformat(t1) < datetime.fromisoformat(cutoff) + timedelta(0)


def test_feedback_does_not_touch_nrm_artifacts(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    _commit(repo, "base")
    (repo / "README.md").write_text("hello there\n", encoding="utf-8")
    settings = _settings(tmp_path)
    temporal = settings.output_dir / "graphs" / "auth" / "temporal_evidence.json"
    temporal.parent.mkdir(parents=True)
    temporal.write_text('{"events": []}\n', encoding="utf-8")
    before = temporal.read_text(encoding="utf-8")
    report = ReviewEngine(settings, cluster=None).run({
        "source": {"kind": "working_tree", "repository": "repo"},
        "verify": False,
        "config": {"policies": [], "verification": {"enabled": False, "commands": []}},
    })
    fingerprint = report["findings"][0]["fingerprint"] if report["findings"] else ""
    if not fingerprint:
        fingerprint = "missing"
        status, payload = record_feedback(settings.output_dir, report["review_id"], {"finding_fingerprint": fingerprint, "status": "false_positive"})
        assert status == 400
    else:
        status, payload = record_feedback(settings.output_dir, report["review_id"], {"finding_fingerprint": fingerprint, "status": "false_positive"})
        assert status == 200
        assert payload["stored"]["nrm_weights_modified"] is False
    assert temporal.read_text(encoding="utf-8") == before
    assert not (settings.output_dir / "phase1_predictions.json").exists()


def _secret_config() -> dict:
    return {
        "policies": [{
            "id": "secret",
            "description": "placeholder",
            "severity": "critical",
            "blocking": True,
            "scope": {"paths": ["**"]},
            "check": {"type": "forbidden_pattern", "patterns": ["PLACEHOLDER_SECRET_VALUE"]},
        }],
        "verification": {"enabled": False, "commands": []},
        "gate": {"block_on_verified_critical": True},
    }


def _node(service: str, name: str, file: str) -> GraphNode:
    qualified = f"{file}::{name}"
    return GraphNode(key=symbol_key(service, file, qualified, "Function"), category=CODE_SYMBOL, service_id=service, name=name, qualified_name=qualified, file_path=file, line_start=1, line_end=3, kind="Function")


def _snap(nodes: list[GraphNode]):
    part = Partition(service_id="auth", repository="auth", workspace_head="head")
    for node in nodes:
        part.nodes[node.key] = node
    return index_snapshot(version="g", generated_at="2024-06-01T00:00:00+00:00", partitions={"auth": part}, overlay={}, bridges=[], diagnostics=[], fingerprints=[])


def _settings(root: Path) -> Settings:
    repos = root / "repos.txt"
    repos.write_text("repo\n", encoding="utf-8")
    output = root / "output"
    output.mkdir(exist_ok=True)
    return Settings(root=root, repos_file=repos, output_dir=output, github_token="", ghe_token="")


def _repo(root: Path) -> Path:
    repo = root / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    return repo


def _commit(repo: Path, message: str, when: str | None = None) -> None:
    env = {}
    if when:
        env = {"GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when}
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", message, env=env)


def _git(repo: Path, *args: str, env: dict | None = None) -> None:
    merged = {**os.environ, "GIT_AUTHOR_NAME": "Nami", "GIT_AUTHOR_EMAIL": "nami@example.com", "GIT_COMMITTER_NAME": "Nami", "GIT_COMMITTER_EMAIL": "nami@example.com"}
    if env:
        merged.update(env)
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=merged)
