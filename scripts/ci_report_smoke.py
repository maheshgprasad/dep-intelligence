#!/usr/bin/env python3
"""Deterministic report and export smoke test.

Uses a temporary directory and fictional services. It does not read or write
the developer's published output, and it does not call a model or a network.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

from dep_intel.config import Settings  # noqa: E402
from dep_intel.cross_repo_graph import index_snapshot  # noqa: E402
from dep_intel.graph_models import CONSUMES_HTTP, HTTP_CONTRACT, GraphEdge, GraphNode, Partition, symbol_key  # noqa: E402
from dep_intel.reporting.builder import generate_report  # noqa: E402
from dep_intel.reporting.package import ensure_export  # noqa: E402
from dep_intel.review_commentary import fallback_commentary  # noqa: E402
from dep_intel.review_export import ensure_export as export_review  # noqa: E402
from dep_intel.service_clusters import detect_service_clusters  # noqa: E402
from dep_intel.store import write_json  # noqa: E402

FIXTURE_NOTE = "CI fixture report. These services are fictional and are not a production analysis."
_KINDS = ("pdf", "markdown", "json", "zip")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dest", type=Path, default=ROOT / "output" / "ci")
    args = parser.parse_args()
    destination = args.dest
    destination.mkdir(parents=True, exist_ok=True)
    samples = destination / "samples"
    if samples.exists():
        shutil.rmtree(samples)
    samples.mkdir(parents=True)
    summary = {
        "workspace": "fail",
        "service_cluster": "fail",
        "repository": "fail",
        "change_review": "fail",
        "pdf": "fail",
        "markdown": "fail",
        "json": "fail",
        "evidence_package": "fail",
    }
    try:
        with tempfile.TemporaryDirectory(prefix="nami-ci-reports-") as temporary:
            root = Path(temporary)
            settings = Settings(root=root, repos_file=root / "repos.txt", output_dir=root, github_token="", ghe_token="")
            snapshot = _fixture()
            groups = detect_service_clusters(snapshot)
            workspace = _annotate(settings, generate_report(settings, snapshot, "workspace"))
            cluster = _annotate(settings, generate_report(settings, snapshot, "service_cluster", cluster_id=groups["clusters"][0]["cluster_id"]))
            repository = _annotate(settings, generate_report(settings, snapshot, "repository", "auth-service"))
            _check_workspace(workspace)
            _check_cluster(cluster)
            _check_repository(repository)
            summary["workspace"] = "pass"
            summary["service_cluster"] = "pass"
            summary["repository"] = "pass"
            exports = {}
            for name, document in (("workspace", workspace), ("service_cluster", cluster), ("repository", repository)):
                exports[name] = {kind: ensure_export(root, document["report_id"], kind) for kind in _KINDS}
                _check_files(document, exports[name])
                _check_text(name, document, exports[name])
            summary["pdf"] = "pass"
            summary["markdown"] = "pass"
            summary["json"] = "pass"
            summary["evidence_package"] = "pass"
            change = _change_review(root)
            summary["change_review"] = "pass"
            _copy_samples(samples, exports, change)
        (destination / "report-smoke.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    except Exception:
        (destination / "report-smoke.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        raise
    print(json.dumps(summary, indent=2))
    return 0


def _annotate(settings: Settings, document: dict) -> dict:
    limitations = list(document.get("limitations") or [])
    if FIXTURE_NOTE not in limitations:
        limitations.append(FIXTURE_NOTE)
    document["limitations"] = limitations
    write_json(settings.output_dir / "reports" / document["report_id"], "report.json", document)
    return document


def _check_workspace(document: dict) -> None:
    services = {item.get("service") for item in document.get("services") or []}
    assert services >= {"auth-service", "user-service", "reporting-service", "tooling-repo"}
    assert document["scope"] == "workspace"
    assert any(item.get("service_id") == "tooling-repo" for item in document.get("standalone") or [])
    assert document.get("service_clusters", {}).get("items")


def _check_cluster(document: dict) -> None:
    members = set(document["identity"]["services"])
    assert members == {"auth-service", "user-service", "reporting-service"}
    assert "tooling-repo" not in members
    assert document["identity"].get("cluster_label")


def _check_repository(document: dict) -> None:
    assert document["scope"] == "repository"
    assert document["identity"]["service"] == "auth-service"


def _check_files(document: dict, paths: dict[str, Path]) -> None:
    expected = {"pdf": ".pdf", "markdown": ".md", "json": ".json", "zip": ".zip"}
    for kind, path in paths.items():
        assert path.is_file() and path.stat().st_size > 0
        assert path.suffix == expected[kind]
        assert document["report_id"] in str(path)


def _check_text(name: str, document: dict, paths: dict[str, Path]) -> None:
    markdown = paths["markdown"].read_text(encoding="utf-8")
    pdf = paths["pdf"].read_bytes().decode("latin-1")
    exported = json.loads(paths["json"].read_text(encoding="utf-8"))
    assert exported["report_id"] == document["report_id"]
    assert exported["scope"] == document["scope"]
    assert FIXTURE_NOTE in markdown
    if name == "workspace":
        assert "Workspace" in markdown and "Detected service clusters" in markdown and "Standalone" in markdown
        assert "Workspace" in pdf and "Standalone" in pdf
    elif name == "service_cluster":
        label = document["identity"]["cluster_label"]
        assert "Service cluster" in markdown and label in markdown
        assert "Service cluster" in pdf and label in pdf
        assert "tooling-repo" not in markdown.split("## Cluster membership", 1)[1].split("##", 1)[0]
    else:
        assert "Repository" in markdown and "Repository" in pdf
        assert exported["identity"]["service"] == "auth-service"


def _change_review(root: Path) -> dict[str, Path]:
    payload = {
        "decision": "warn",
        "service_id": "auth-service",
        "triggered_rules": [{"rule_id": "BG-TEMPORAL-001", "title": "Historical association is not a blocker"}],
        "evidence": [{"file_path": "src/app.py", "statement": "Repository history identified src/app.py."}],
        "gate_validation": {"status": "validated"},
    }
    commentary = fallback_commentary(payload)
    review_id = "rev-ci-fixture"
    report = {
        "review_id": review_id,
        "change": {"repository": "auth-service", "service_id": "auth-service", "base_sha": "a", "head_sha": "b"},
        "gate": {"decision": "warn", "decision_rules": [{"rule_id": "BG-TEMPORAL-001", "title": "Historical association is not a blocker", "outcome": "triggered"}]},
        "assessment": {"headline": commentary["headline"], "summary": commentary["summary"]},
        "commentary": commentary,
        "actions": [{"title": commentary["next_action"], "description": commentary["next_action"], "required": False}],
        "evidence_catalog": [{"evidence_id": "EV-1", "statement": "Repository history identified src/app.py.", "source": "git_history", "file_path": "src/app.py"}],
        "gate_validation": {"status": "validated", "summary": "Decision logic validated."},
        "limitations": [FIXTURE_NOTE],
    }
    directory = root / "reviews" / review_id
    directory.mkdir(parents=True)
    (directory / "report.json").write_text(json.dumps(report), encoding="utf-8")
    (directory / "manifest.json").write_text(json.dumps({"review_id": review_id}), encoding="utf-8")
    (directory / "feedback.json").write_text("{}", encoding="utf-8")
    paths = {kind: export_review(root, review_id, kind) for kind in ("pdf", "markdown", "json")}
    pdf = paths["pdf"].read_bytes().decode("latin-1")
    markdown = paths["markdown"].read_text(encoding="utf-8")
    body = json.loads(paths["json"].read_text(encoding="utf-8"))
    combined = pdf + "\n" + markdown + "\n" + json.dumps(body)
    assert body["gate_decision"] == "warn"
    assert "WARN" in combined or "warn" in combined
    assert "Nami explanation" in combined
    assert "Decision logic validation" in combined
    assert "src/app.py" in combined
    assert "Review" in combined
    assert FIXTURE_NOTE in json.dumps(body)
    return paths


def _copy_samples(samples: Path, exports: dict, change: dict[str, Path]) -> None:
    names = {
        "workspace": "nami-workspace-sample",
        "service_cluster": "nami-service-cluster-sample",
        "repository": "nami-repository-sample",
    }
    for scope, stem in names.items():
        for kind, suffix in (("pdf", ".pdf"), ("markdown", ".md"), ("json", ".json")):
            shutil.copyfile(exports[scope][kind], samples / f"{stem}{suffix}")
    shutil.copyfile(change["pdf"], samples / "nami-change-review-sample.pdf")
    shutil.copyfile(change["markdown"], samples / "nami-change-review-sample.md")
    shutil.copyfile(change["json"], samples / "nami-change-review-sample.json")
    (samples / "README.txt").write_text(
        "CI fixture reports. These files use fictional services and are not a production analysis.\n",
        encoding="utf-8",
    )


def _fixture():
    services = ["auth-service", "user-service", "reporting-service", "tooling-repo"]
    partitions = {
        service: Partition(service_id=service, repository=service, workspace_head=f"head-{service}", quality="ok")
        for service in services
    }
    edges = []
    for source, target in (("auth-service", "user-service"), ("user-service", "reporting-service")):
        identity = f"http::GET:/{target}"
        contract = GraphNode(
            key=symbol_key(target, "", identity, HTTP_CONTRACT),
            category=HTTP_CONTRACT,
            service_id=target,
            name=f"GET /{target}",
            qualified_name=identity,
            file_path="src/app.py",
            kind=HTTP_CONTRACT,
            metadata={"identity": identity},
        )
        caller = GraphNode(
            key=symbol_key(source, "src/app.py", f"{source}::call", "Function"),
            category="code_symbol",
            service_id=source,
            name="call",
            qualified_name=f"{source}::call",
            file_path="src/app.py",
            kind="Function",
        )
        partitions[target].nodes[contract.key] = contract
        partitions[source].nodes[caller.key] = caller
        edges.append(GraphEdge(caller.key, contract.key, CONSUMES_HTTP, {"status": "declared", "resolution": "exact"}))
    return index_snapshot(
        version="ci-fixture",
        generated_at="2026-01-01T00:00:00+00:00",
        partitions=partitions,
        overlay={},
        bridges=edges,
        diagnostics=[],
        fingerprints=[],
    )


if __name__ == "__main__":
    raise SystemExit(main())
