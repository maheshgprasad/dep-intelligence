"""Evidence grades, gate traceability, and stored-review exports."""

from __future__ import annotations

import io
import json
import sys
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from dep_intel.api import app
from dep_intel.change_view import build_change_view
from dep_intel.config import PROJECT_ROOT, Settings
from dep_intel.cross_repo_graph import index_snapshot
from dep_intel.graph_models import CONSUMES_HTTP, HTTP_CONTRACT, GraphEdge, GraphNode, Partition, symbol_key
from dep_intel.review_engine import ReviewEngine
from dep_intel.review_evidence import apply_evidence_gate
from dep_intel.review_explain import attach_explanation, enforcement_exit, summarize_impact
from dep_intel.review_export import canonical_export, ensure_export
from dep_intel.review_gate import decide_gate
from dep_intel.review_sufficiency import apply_claim_sufficiency, grade_claim

FIXTURE = (PROJECT_ROOT / "fixtures/auth-service/src/server.js").read_text(encoding="utf-8")
REMOVED = FIXTURE.replace('app.post("/api/auth/validate", (req, res) => {\n  res.json({ ok: true });\n});\n\n', "")
BODY = FIXTURE.replace("{ ok: true }", "{ ok: true, edited: true }")
GATE = {"gate": {"strict_graph": False, "block_on_verified_critical": True, "block_on_verified_high_blocker_policy": True, "block_on_breaking_contract_with_known_consumers": True}, "review": {"max_findings": 20}}


def test_required_evidence_is_verified_and_missing_evidence_is_downgraded() -> None:
    complete = [
        {"kind": "contract_removed", "source": "diff", "statement": "POST /api/auth/validate was removed.", "supports": ["consumed_contract_break"]},
        {"kind": "known_consumer", "source": "cluster_graph", "statement": "user-service consumes the operation.", "supports": ["consumed_contract_break"]},
    ]
    verified = grade_claim({"type": "consumed_contract_break"}, complete, alignment="exact")
    assert verified["grade"] == "verified"
    assert verified["status"] == "accepted"
    missing = grade_claim({"type": "consumed_contract_break"}, complete[:1], alignment="exact")
    assert missing["status"] == "downgraded"
    assert missing["grade"] in {"weak", "unsupported"}
    assert "known_consumer" in missing["missing"]


def test_counter_evidence_suppresses_a_claim() -> None:
    evidence = [
        {"kind": "contract_removed", "source": "diff", "supports": ["consumed_contract_break"], "statement": "Removed."},
        {"kind": "known_consumer", "source": "cluster_graph", "supports": ["consumed_contract_break"], "statement": "user-service consumes it."},
        {"kind": "openapi_operation_present", "source": "OpenAPI", "contradicts": ["consumed_contract_break"], "statement": "HEAD OpenAPI still defines the operation."},
    ]
    graded = grade_claim({"type": "consumed_contract_break"}, evidence, alignment="exact")
    assert graded["status"] == "suppressed"
    finding = {
        "finding_id": "claim-1",
        "category": "contract_break",
        "severity": "high",
        "title": "Removed POST /api/auth/validate",
        "claim": "Removed POST /api/auth/validate",
        "evidence_strength": "verified",
        "blocking": True,
        "source": "diff",
        "file_path": "src/server.js",
        "evidence": [{"source": "diff", "summary": "Removed POST /api/auth/validate", "structured_data": {"consumers": [{"service_id": "user-service"}]}}],
        "structured": {"contract": {"consumers": [{"service_id": "user-service"}]}},
        "counter_evidence": [{"kind": "openapi_operation_present", "source": "OpenAPI", "contradicts": ["consumed_contract_break"], "statement": "HEAD OpenAPI still defines the operation."}],
    }
    accepted, rejected = apply_claim_sufficiency([finding], alignment="exact")
    assert accepted == []
    assert rejected[0]["suppressed"] is True


def test_stale_temporal_and_model_evidence_cannot_verify_a_blocker() -> None:
    stale = grade_claim(
        {"type": "consumed_contract_break"},
        [
            {"kind": "contract_removed", "source": "diff", "supports": ["consumed_contract_break"]},
            {"kind": "known_consumer", "source": "cluster_graph", "supports": ["consumed_contract_break"]},
        ],
        alignment="stale",
    )
    assert stale["grade"] == "moderate"
    temporal = {
        "finding_id": "t",
        "category": "review_target",
        "severity": "info",
        "title": "history",
        "claim": "history",
        "evidence_strength": "verified",
        "source": "NRM",
        "file_path": "src/users.js",
        "evidence": [{"source": "NRM", "summary": "history"}],
        "structured": {"evidence_origin": "temporal_only", "hybrid_score": 0.99},
    }
    kept, _rejected = apply_claim_sufficiency([temporal], alignment="exact")
    assert kept[0]["evidence_strength"] == "weak"
    assert decide_gate(kept, [], GATE, alignment="exact", diagnostics=[])["decision"] != "block"
    model = {
        "finding_id": "m",
        "category": "regression_risk",
        "severity": "high",
        "title": "model",
        "claim": "maybe",
        "evidence_strength": "verified",
        "blocking": True,
        "source": "LLM",
        "file_path": "src/server.js",
        "evidence": [{"source": "LLM", "summary": "maybe"}],
        "structured": {"advisory": True},
    }
    graded = grade_claim({"type": "llm_advisory"}, model["evidence"], alignment="exact")
    assert graded["grade"] != "verified"
    kept_model, _rejected = apply_claim_sufficiency([model], alignment="exact")
    assert kept_model[0]["evidence_strength"] != "verified"
    assert decide_gate(kept_model, [], GATE, alignment="exact", diagnostics=[])["decision"] != "block"


def test_contract_removal_blocks_with_rule_evidence_and_action(tmp_path: Path) -> None:
    report = _run(tmp_path, REMOVED, candidates=[_candidate("both")])
    assert report["gate"]["decision"] == "block"
    kinds = {item["type"] for item in report["inferences"]}
    assert "consumer_compatibility_break" in kinds
    rules = [item["rule_id"] for item in report["gate"]["decision_rules"]]
    assert "BG-CONTRACT-001" in rules
    rule = next(item for item in report["gate"]["decision_rules"] if item["rule_id"] == "BG-CONTRACT-001")
    assert rule["evidence_refs"]
    assert rule["required_actions"]
    required = [item for item in report["actions"] if item["required"]]
    text = " ".join(item["title"] + item["description"] for item in required).lower()
    assert "restore" in text or "update" in text
    assert "user-service" in text
    ids = {item["evidence_id"] for item in report["evidence_catalog"]}
    assert rule["evidence_refs"][0] in ids
    assert report["decision_presentation"]["heading"] == "Action required before proceeding"
    assert report["enforcement"]["gate_decision"] == "block"
    assert report["enforcement"]["enforced"] is False


def test_body_edit_does_not_claim_a_contract_break(tmp_path: Path) -> None:
    report = _run(tmp_path, BODY, candidates=[_candidate("structural_only")])
    assert report["gate"]["decision"] in {"warn", "pass"}
    assert report["gate"]["decision"] != "block"
    assert not any(item.get("category") == "contract_break" for item in report["findings"])
    assert "no verified breaking contract" in report["assessment"]["summary"].lower() or "downstream" in report["assessment"]["summary"].lower()


def test_temporal_only_is_a_review_target_and_never_blocks(tmp_path: Path) -> None:
    report = _run(tmp_path, BODY, candidates=[_candidate("temporal_only")])
    assert report["gate"]["decision"] != "block"
    assert any(item["type"] == "historical_association_only" for item in report["inferences"])
    assert "BG-CONTRACT-001" not in [item["rule_id"] for item in report["gate"]["decision_rules"]]
    temporal = next(item for item in report["gate"]["decision_rules"] if item["rule_id"] == "BG-TEMPORAL-001")
    assert temporal["blocks"] is False


def test_pass_checklist_records_checks_that_ran(tmp_path: Path) -> None:
    report = _run(tmp_path, BODY, candidates=[], verify=_verify(0))
    assert report["gate"]["decision"] == "pass"
    checklist = {item["check_id"]: item["status"] for item in report["gate_checklist"]}
    assert checklist["CHECK-GRAPH"] == "passed"
    assert checklist["CHECK-CONTRACT"] == "passed"
    assert checklist["CHECK-VERIFY"] == "passed"
    assert report["decision_presentation"]["label"] == "PASS"


def test_incomplete_explains_the_missing_graph(tmp_path: Path) -> None:
    report = _run(tmp_path, BODY, candidates=[], head="other")
    assert report["gate"]["decision"] == "incomplete"
    assert report["gate"]["decision"] != "block"
    text = report["assessment"]["summary"].lower()
    assert "graph" in text
    assert report["assessment"]["recommended_next_step"]
    assert any(item["rule_id"] == "BG-GRAPH-001" for item in report["gate"]["decision_rules"])
    assert any(item["required"] for item in report["actions"])


def test_required_verification_failure_blocks_with_command_evidence(tmp_path: Path) -> None:
    report = _run(tmp_path, BODY, candidates=[], verify=_verify(3))
    assert report["gate"]["decision"] == "block"
    assert any(item["type"] == "verification_failed" for item in report["inferences"])
    rule = next(item for item in report["gate"]["decision_rules"] if item["rule_id"] == "BG-VERIFY-001")
    assert rule["evidence_refs"]
    action = " ".join(f"{item['title']} {item['description']}" for item in report["actions"] if item["required"]).lower()
    assert "verification" in action
    stored = json.loads((tmp_path / "output" / "reviews" / report["review_id"] / "verification.json").read_text(encoding="utf-8"))
    assert stored["commands"][0]["exit_code"] == 3


def test_exports_match_the_stored_decision_and_omit_secrets(tmp_path: Path) -> None:
    report = _run(tmp_path, REMOVED, candidates=[_candidate("both")])
    report_id = report["review_id"]
    output = tmp_path / "output"
    payload = canonical_export(report, {"config_hash": "abc", "completed_at": "2026-01-01T00:00:00+00:00"}, {"items": []})
    assert payload["schema"] == "nami.review.export"
    assert payload["schema_version"] == 1
    assert payload["gate_decision"] == report["gate"]["decision"] == "block"
    assert payload["identity"]["review_id"] == report_id
    evidence_ids = {item["evidence_id"] for item in payload["evidence"]}
    for inference in payload["inferences"]:
        assert set(inference["evidence_refs"]) <= evidence_ids
    again = canonical_export(report, {"config_hash": "abc", "completed_at": "2026-01-01T00:00:00+00:00"}, {"items": []})
    assert again["reproducibility"]["report_hash"] == payload["reproducibility"]["report_hash"]
    assert payload["reproducibility"]["report_hash"].startswith("sha256:")
    poisoned = json.loads(json.dumps(report))
    poisoned["diagnostics"].append({"code": "note", "message": "token ghp_abcdefghijklmnopqrstuvwxyz012345", "severity": "info"})
    scrubbed = canonical_export(poisoned, {}, {"items": []})
    assert "ghp_" not in json.dumps(scrubbed)
    pdf = ensure_export(output, report_id, "pdf")
    data = pdf.read_bytes()
    assert data.startswith(b"%PDF")
    assert report_id.encode("ascii") in data
    assert b"BLOCK" in data
    package = ensure_export(output, report_id, "zip")
    with zipfile.ZipFile(io.BytesIO(package.read_bytes())) as archive:
        names = archive.namelist()
    assert any(name.endswith("/report.pdf") for name in names)
    assert any(name.endswith("/report.json") for name in names)
    assert any(name.endswith("/gate/decision.json") for name in names)
    assert any(name.endswith("/evidence/suppressed-findings.json") for name in names)
    assert not any(".env" in name or "worktree" in name for name in names)
    markdown = (output / "reviews" / report_id / "report.md").read_text(encoding="utf-8")
    assert "Assessment" in markdown
    assert "BG-CONTRACT-001" in markdown


def test_suppressed_claim_is_in_the_audit_export() -> None:
    finding = {
        "finding_id": "gone",
        "category": "contract_break",
        "claim": "Removed POST /api/auth/validate",
        "title": "Removed POST /api/auth/validate",
        "file_path": "src/server.js",
        "line_start": 1,
        "source": "diff",
        "evidence_strength": "verified",
        "evidence": [{"source": "diff", "summary": "Removed POST /api/auth/validate"}],
        "structured": {"contract": {"kind": "operation_removed", "method": "POST", "path": "/api/auth/validate"}},
    }
    change = {"changed_files": [{"new_path": "src/server.js"}], "contract_changes": []}
    _accepted, rejected = apply_evidence_gate([finding], change, None)
    report = attach_explanation({
        "review_id": "suppressed",
        "gate": {"decision": "pass", "reason_codes": [], "blocking_findings": [], "warning_findings": [], "review_targets": []},
        "findings": [],
        "diagnostics": rejected,
        "change": {"graph_alignment": "exact", "service_id": "auth-service"},
        "contract_changes": [],
        "verification": [],
        "policies": [],
        "impact": {"affected_services": [], "ranked_candidates": []},
    }, {"enforcement": {"mode": "advisory"}})
    assert report["gate"]["decision"] == "pass"
    assert report["suppressed_findings"]
    exported = canonical_export(report, {}, {})
    assert exported["suppressed_findings"]
    assert "still present" in exported["suppressed_findings"][0]["reason"].lower()


def test_high_rank_alone_does_not_add_a_blocking_rule() -> None:
    finding = {
        "finding_id": "rank",
        "category": "review_target",
        "severity": "info",
        "title": "Review src/users.js",
        "claim": "review target",
        "evidence_strength": "moderate",
        "source": "NRM",
        "file_path": "src/users.js",
        "service_id": "user-service",
        "evidence": [{"source": "NRM", "summary": "Reachable."}],
        "structured": {"evidence_origin": "structural_only", "hybrid_score": 0.99, "rank": 1},
    }
    gate = decide_gate([finding], [], GATE, alignment="exact", diagnostics=[])
    assert gate["decision"] != "block"
    report = attach_explanation({
        "review_id": "rank",
        "gate": gate,
        "findings": gate["findings"],
        "change": {"service_id": "auth-service", "graph_alignment": "exact"},
        "impact": {"affected_services": ["user-service"], "ranked_candidates": []},
        "contract_changes": [],
        "verification": [],
        "policies": [],
        "diagnostics": [],
    })
    assert "BG-CONTRACT-001" not in [item["rule_id"] for item in report["gate"]["decision_rules"]]
    assert all(not item.get("blocks") for item in report["gate"]["decision_rules"])


def test_enforcement_modes_do_not_change_the_gate_decision() -> None:
    assert enforcement_exit("block", "advisory") == 0
    assert enforcement_exit("block", "merge_gate") == 1
    assert enforcement_exit("warn", "deployment_gate") == 0
    assert enforcement_exit("incomplete", "merge_gate", "hold") == 2
    assert enforcement_exit("incomplete", "deployment_gate", "allow") == 0


def test_impact_summary_does_not_claim_a_break() -> None:
    summary = summarize_impact({
        "root": {"service_id": "auth-service"},
        "candidates": [{"service_id": "user-service", "file_path": "src/users.js", "evidence_origin": "both", "hybrid_score": 0.9}],
    })
    assert "break" not in summary["statement"].lower()
    assert "user-service" in summary["statement"] or "downstream" in summary["statement"]
    historical = summarize_impact({
        "root": {"service_id": "auth-service"},
        "candidates": [{"service_id": "user-service", "file_path": "src/users.js", "evidence_origin": "temporal_only", "hybrid_score": 0.4}],
    })
    assert historical["title"] == "Historically associated review target"
    assert "confirmed" in historical["statement"].lower()


def test_export_endpoints_use_the_stored_review(tmp_path: Path, monkeypatch) -> None:
    report = _run(tmp_path, REMOVED, candidates=[_candidate("both")])
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "output"))
    client = TestClient(app)
    review_id = report["review_id"]
    pdf = client.get(f"/api/review/{review_id}/export/pdf")
    assert pdf.status_code == 200
    assert pdf.headers["content-type"].startswith("application/pdf")
    assert f"nami-review-{review_id[:8]}.pdf" in pdf.headers.get("content-disposition", "")
    assert pdf.content.startswith(b"%PDF")
    exported = client.get(f"/api/review/{review_id}/export/json")
    assert exported.status_code == 200
    body = exported.json()
    assert body["schema_version"] == 1
    assert body["gate_decision"] == "block"
    package = client.get(f"/api/review/{review_id}/export/evidence")
    assert package.status_code == 200
    assert package.headers["content-type"].startswith("application/zip")


def test_change_view_still_distinguishes_removal_from_a_body_edit() -> None:
    removed = build_change_view(_resolved(FIXTURE, REMOVED), service_id="auth-service", repository="fixtures/auth-service", snapshot=_snapshot(), base_snapshot=None, repo_root=Path("."))
    assert any(item["kind"] == "operation_removed" and item["consumers"] for item in removed["contract_changes"])
    edited = build_change_view(_resolved(FIXTURE, BODY), service_id="auth-service", repository="fixtures/auth-service", snapshot=_snapshot(), base_snapshot=None, repo_root=Path("."))
    assert not any(item["breaking"] for item in edited["contract_changes"])


def _run(tmp_path: Path, new_text: str, *, candidates: list[dict], verify: dict | None = None, head: str = "headsha") -> dict:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "src").mkdir()
    (repo / "src" / "server.js").write_text(new_text, encoding="utf-8")
    repos = tmp_path / "repos.txt"
    repos.write_text("repo\n", encoding="utf-8")
    output = tmp_path / "output"
    output.mkdir()
    settings = Settings(root=tmp_path, repos_file=repos, output_dir=output, github_token="", ghe_token="")
    snapshot = _snapshot(head=head if head == "headsha" else "published")
    config = {"policies": [], "verification": verify["verification"] if verify else {"enabled": False, "commands": []}, "gate": {"strict_graph": head != "headsha"}}
    engine = ReviewEngine(settings, cluster=_Cluster(snapshot, candidates))
    return engine.run({
        "source": {
            "kind": "explicit",
            "repository": "repo",
            "service_id": "auth-service",
            "base_ref": "basesha",
            "head_ref": "headsha",
            "files": [{"path": "src/server.js", "old_text": FIXTURE, "new_text": new_text, "change_type": "modified"}],
        },
        "service": "auth-service",
        "mode": "fast",
        "verify": bool(verify),
        "model": {"enabled": False},
        "config": config,
    })


def _verify(exit_code: int) -> dict:
    command = [sys.executable, "-c", f"import sys; raise SystemExit({exit_code})"]
    return {"verification": {"enabled": True, "allow_network": False, "timeout_seconds": 30, "commands": [{"id": "auth-tests", "command": command, "cwd": ".", "required": True, "paths": ["**"]}]}}


def _candidate(origin: str) -> dict:
    return {
        "service_id": "user-service",
        "repository": "user-service",
        "file_path": "src/users.js",
        "name": "listUsers",
        "hybrid_score": 0.9,
        "evidence_origin": origin,
        "structural": {"score": 0.8 if origin != "temporal_only" else 0, "available": origin != "temporal_only", "path": []},
        "temporal": {"score": 0.2, "available": origin != "structural_only", "observation": "Historical association only."},
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


def _resolved(old: str, new: str) -> dict:
    return {"kind": "explicit", "explicit_files": [{"path": "src/server.js", "old_text": old, "new_text": new, "change_type": "modified"}], "diagnostics": [], "base_sha": "basesha", "head_sha": "headsha"}


def _snapshot(head: str = "headsha"):
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
    auth = Partition(service_id="auth-service", repository="repo", workspace_head=head)
    user = Partition(service_id="user-service", repository="user-service", workspace_head=head)
    auth.nodes[contract.key] = contract
    user.nodes[caller.key] = caller
    edge = GraphEdge(caller.key, contract.key, CONSUMES_HTTP, {"status": "extracted"})
    return index_snapshot(version="g", generated_at="2026-01-01T00:00:00+00:00", partitions={"auth-service": auth, "user-service": user}, overlay={}, bridges=[edge], diagnostics=[], fingerprints=[])
