"""Production-path cluster dependency comparison.

These tests build real git trees and run the cluster review boundary builder.
They do not hand the analyzer a fabricated consumer action as the only proof.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import zipfile
from pathlib import Path

from dep_intel.cluster_dependency_state import (
    build_cluster_dependency_state,
    build_review_boundaries,
    canonical_contract_identity,
    canonical_relationship_key,
    synthetic_manifest,
    version_successor,
)
from dep_intel.cluster_interdependencies import analyze_interdependencies, evidence_zip, render_json, render_markdown, render_pdf
from dep_intel.cluster_review import execute_cluster_review, load_cluster_review, new_cluster_review
from dep_intel.config import Settings

PROVIDER_V1 = """const express = require("express");
const app = express();
app.post("/v1/test", function validate(req, res) { res.json({ ok: true }); });
"""
PROVIDER_V2 = """const express = require("express");
const app = express();
app.post("/v2/test", function validate(req, res) { res.json({ ok: true }); });
"""
CONSUMER_V1 = """const axios = require("axios");
axios.post("http://provider/v1/test", {});
"""
CONSUMER_V2 = """const axios = require("axios");
axios.post("http://provider/v2/test", {});
"""
LOGIN = """const express = require("express");
const app = express();
app.post("/login", function login(req, res) { res.json({ ok: true }); });
app.get("/health", function health(req, res) { res.json({ ok: true }); });
"""
METRICS = """const express = require("express");
const app = express();
app.post("/metrics", function metrics(req, res) { res.json({ ok: true }); });
app.get("/health", function health(req, res) { res.json({ ok: true }); });
"""
LOGIN_CLIENT = """const axios = require("axios");
axios.post("http://provider/login", {});
axios.get("http://provider/health", {});
"""
METRICS_CLIENT = """const axios = require("axios");
axios.post("http://provider/metrics", {});
axios.get("http://provider/health", {});
"""


def _git(repo: Path, *args: str, when: str = "") -> str:
    env = os.environ.copy()
    env["GIT_AUTHOR_NAME"] = "Nami Test"
    env["GIT_AUTHOR_EMAIL"] = "nami@example.com"
    env["GIT_COMMITTER_NAME"] = "Nami Test"
    env["GIT_COMMITTER_EMAIL"] = "nami@example.com"
    if when:
        env["GIT_AUTHOR_DATE"] = when
        env["GIT_COMMITTER_DATE"] = when
    completed = subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)
    return completed.stdout.decode()


def _init(path: Path) -> None:
    path.mkdir(parents=True)
    subprocess.run(["git", "-C", str(path), "init"], check=True, capture_output=True)
    _git(path, "config", "user.email", "nami@example.com")
    _git(path, "config", "user.name", "Nami Test")


def _write(repo: Path, relative: str, text: str) -> None:
    target = repo / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def _commit(repo: Path, message: str, when: str) -> str:
    _git(repo, "add", "-A", when=when)
    _git(repo, "commit", "-m", message, when=when)
    return _git(repo, "rev-parse", "HEAD", when=when).strip()


def _settings(tmp: Path) -> Settings:
    output = tmp / "output"
    output.mkdir(exist_ok=True)
    return Settings(root=tmp, repos_file=tmp / "repos.txt", output_dir=output, github_token="", ghe_token="")


def _review(member: dict, _body: dict) -> dict:
    service = member["service_id"]
    blocked = service == "provider"
    return {
        "review_id": f"rev-{service}",
        "gate": {"decision": "block" if blocked else "pass", "reason_codes": ["BG-CONTRACT-001"] if blocked else []},
        "gate_validation": {"status": "validated"},
        "review_scope": {"affected_member_services": []},
        "contract_changes": [],
    }


def _run(tmp: Path, source: dict, members: list[dict]) -> dict:
    record = new_cluster_review(cluster_id="demo", cluster_label="Demo", source=source, members=members)
    return execute_cluster_review(
        record,
        settings=_settings(tmp),
        body={"source": source, "mode": "fast", "verify": False},
        publish=lambda _name, _data: None,
        review=_review,
    )


def _by(result: dict, classification: str, consumer: str = "", contract: str = "") -> dict:
    rows = [
        item for item in result["interdependencies"]["items"]
        if item["classification"] == classification
        and (not consumer or item["consumer_service"] == consumer)
        and (not contract or item["contract_id"] == contract)
    ]
    assert rows, (classification, consumer, contract, [(item["classification"], item["consumer_service"], item["contract_id"]) for item in result["interdependencies"]["items"]])
    return rows[0]


def test_canonical_identity_matches_graph_rows_and_contract_changes() -> None:
    graph = {"contract_id": "auth:src/server.js:http::POST:/v1/validate:http_contract", "contract": "POST /v1/validate", "contract_identity": "http::POST:/v1/validate"}
    change = {"method": "post", "path": "/v1/validate"}
    assert canonical_contract_identity(graph) == canonical_contract_identity(change) == "POST /v1/validate"
    assert canonical_relationship_key("http_contract", "provider", "consumer", "POST /v1/validate") == (
        "http_contract", "provider", "consumer", "POST /v1/validate",
    )
    assert version_successor("POST /v1/test", "POST /v2/test")
    assert not version_successor("POST /login", "POST /metrics")


def test_timeframe_migration_leaves_unchanged_consumer_broken(tmp_path: Path) -> None:
    provider = tmp_path / "provider"
    consumer_a = tmp_path / "consumer-a"
    consumer_b = tmp_path / "consumer-b"
    for repo, filename, text in (
        (provider, "src/server.js", PROVIDER_V1),
        (consumer_a, "src/client.js", CONSUMER_V1),
        (consumer_b, "src/client.js", CONSUMER_V1),
    ):
        _init(repo)
        _write(repo, filename, text)
        _commit(repo, "baseline", "2026-10-01T12:00:00")
    _write(provider, "src/server.js", PROVIDER_V2)
    _commit(provider, "move to v2", "2026-11-10T12:00:00")
    _write(consumer_a, "src/client.js", CONSUMER_V2)
    _commit(consumer_a, "call v2", "2026-11-11T12:00:00")
    result = _run(
        tmp_path,
        {"kind": "timeframe", "since": "2026-11-01", "until": "2026-11-21"},
        [
            {"service_id": "provider", "repository": "provider", "repository_path": str(provider)},
            {"service_id": "consumer-a", "repository": "consumer-a", "repository_path": str(consumer_a)},
            {"service_id": "consumer-b", "repository": "consumer-b", "repository_path": str(consumer_b)},
        ],
    )
    analysis = result["interdependencies"]
    assert analysis["comparison_mode"] == "exact_boundary_snapshots"
    provider_row = next(item for item in result["repositories"] if item["service_id"] == "provider")
    consumer_a_row = next(item for item in result["repositories"] if item["service_id"] == "consumer-a")
    consumer_b_row = next(item for item in result["repositories"] if item["service_id"] == "consumer-b")
    assert provider_row["decision"] == "block"
    assert consumer_a_row["decision"] == "pass"
    assert consumer_b_row["status"] == "no_change"
    assert consumer_b_row["decision"] == ""
    assert _by(result, "coordinated_change", "consumer-a", "POST /v1/test")
    assert _by(result, "introduced", "consumer-a", "POST /v2/test")
    broken = _by(result, "broken", "consumer-b", "POST /v1/test")
    assert broken["after"]["provider_present"] is False
    assert broken["after"]["consumer_reference_present"] is True
    assert "consumer-b" in analysis["unchanged_but_affected_services"]
    assert result["assessment"]["label"] == "ATTENTION REQUIRED"
    stored = load_cluster_review(tmp_path / "output", result["cluster_review_id"])
    assert stored["interdependencies"]["comparison_mode"] == "exact_boundary_snapshots"
    assert stored["interdependencies"]["comparison_coverage"]["services_exact_before"] == 3
    package = evidence_zip(stored)
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        names = set(archive.namelist())
        assert names >= {
            "cluster-review.json",
            "cluster-review.md",
            "change-set.json",
            "before-state.json",
            "after-state.json",
            "interdependencies.json",
            "cluster-evidence.json",
        }
        evidence = json.loads(archive.read("cluster-evidence.json"))
        match = next(item for item in evidence if item["evidence_id"] == broken["evidence_ids"][0])
        assert match["after"]["provider_present"] is False
        assert match["after"]["consumer_reference_present"] is True
        assert "/home/" not in archive.read("before-state.json").decode()
    markdown = render_markdown(stored)
    payload = render_json(stored)
    pdf = render_pdf(stored)
    assert payload["summary"]["broken"] == analysis["summary"]["broken"] == 1
    assert "Broken" in markdown
    assert b"ATTENTION REQUIRED" in pdf
    assert payload["comparison_mode"] == "exact_boundary_snapshots"
    assert "consumer-b" in payload["unchanged_but_affected_services"]


def test_working_tree_break_and_staged_ignores_unstaged_restore(tmp_path: Path) -> None:
    provider = tmp_path / "provider"
    consumer = tmp_path / "consumer"
    _init(provider)
    _init(consumer)
    _write(provider, "src/server.js", PROVIDER_V1)
    _write(consumer, "src/client.js", CONSUMER_V1)
    _commit(provider, "baseline", "2026-10-01T12:00:00")
    _commit(consumer, "baseline", "2026-10-01T12:00:00")
    _write(provider, "src/server.js", PROVIDER_V2)
    working = _run(
        tmp_path,
        {"kind": "working_tree"},
        [
            {"service_id": "provider", "repository": "provider", "repository_path": str(provider)},
            {"service_id": "consumer", "repository": "consumer", "repository_path": str(consumer)},
        ],
    )
    assert next(item["status"] for item in working["repositories"] if item["service_id"] == "consumer") == "no_change"
    assert _by(working, "broken", "consumer", "POST /v1/test")
    _git(provider, "add", "src/server.js")
    _write(provider, "src/server.js", PROVIDER_V1)
    _write(provider, "src/noise.js", "const express = require('express'); const app = express(); app.post('/unstaged', function noise(req, res) {});")
    staged = _run(
        tmp_path,
        {"kind": "staged"},
        [
            {"service_id": "provider", "repository": "provider", "repository_path": str(provider)},
            {"service_id": "consumer", "repository": "consumer", "repository_path": str(consumer)},
        ],
    )
    assert _by(staged, "broken", "consumer", "POST /v1/test")
    assert not any(item["contract_id"] == "POST /unstaged" for item in staged["interdependencies"]["items"])


def test_commit_does_not_apply_sha_to_other_repositories(tmp_path: Path) -> None:
    provider = tmp_path / "provider"
    consumer = tmp_path / "consumer"
    _init(provider)
    _init(consumer)
    _write(provider, "src/server.js", PROVIDER_V1)
    _write(consumer, "src/client.js", CONSUMER_V1)
    _commit(provider, "baseline", "2026-10-01T12:00:00")
    _commit(consumer, "baseline", "2026-10-01T12:00:00")
    _write(provider, "src/server.js", PROVIDER_V2)
    sha = _commit(provider, "remove v1", "2026-11-02T12:00:00")
    result = _run(
        tmp_path,
        {"kind": "commit", "commit": sha, "repository": "provider"},
        [
            {"service_id": "provider", "repository": "provider", "repository_path": str(provider)},
            {"service_id": "consumer", "repository": "consumer", "repository_path": str(consumer)},
        ],
    )
    consumer_row = next(item for item in result["repositories"] if item["service_id"] == "consumer")
    assert consumer_row["status"] == "no_change"
    assert consumer_row["error_code"] == ""
    assert "bad object" not in (consumer_row["error_summary"] or "")
    assert _by(result, "broken", "consumer", "POST /v1/test")


def test_false_migration_is_not_coordinated(tmp_path: Path) -> None:
    before = tmp_path / "before"
    after = tmp_path / "after"
    (before / "provider").mkdir(parents=True)
    (before / "consumer").mkdir(parents=True)
    (after / "provider").mkdir(parents=True)
    (after / "consumer").mkdir(parents=True)
    (before / "provider" / "src").mkdir()
    (before / "consumer" / "src").mkdir()
    (after / "provider" / "src").mkdir()
    (after / "consumer" / "src").mkdir()
    (before / "provider" / "src" / "server.js").write_text(LOGIN, encoding="utf-8")
    (before / "consumer" / "src" / "client.js").write_text(LOGIN_CLIENT, encoding="utf-8")
    (after / "provider" / "src" / "server.js").write_text(METRICS, encoding="utf-8")
    (after / "consumer" / "src" / "client.js").write_text(METRICS_CLIENT, encoding="utf-8")
    manifest = synthetic_manifest(["provider", "consumer"])
    before_state = build_cluster_dependency_state(
        {"provider": before / "provider", "consumer": before / "consumer"},
        state_kind="before",
        source_kind="working_tree",
        manifest=manifest,
    )
    after_state = build_cluster_dependency_state(
        {"provider": after / "provider", "consumer": after / "consumer"},
        state_kind="after",
        source_kind="working_tree",
        manifest=manifest,
    )
    result = analyze_interdependencies(
        [{"service_id": "provider", "changed": True}, {"service_id": "consumer", "changed": True}],
        before_state=before_state,
        after_state=after_state,
        comparison_mode="exact_boundary_snapshots",
    )
    assert not any(item["classification"] == "coordinated_change" for item in result["items"])
    assert any(item["classification"] == "relationship_removed" and item["contract_id"] == "POST /login" for item in result["items"])
    assert any(item["classification"] == "introduced" and item["contract_id"] == "POST /metrics" for item in result["items"])


def test_new_unresolved_reference_is_not_a_healthy_introduction(tmp_path: Path) -> None:
    root = tmp_path / "consumer"
    root.mkdir()
    (root / "src").mkdir()
    (root / "src" / "client.js").write_text(CONSUMER_V1, encoding="utf-8")
    empty = tmp_path / "provider"
    empty.mkdir()
    (empty / "src").mkdir()
    (empty / "src" / "server.js").write_text("const express = require('express'); const app = express();\n", encoding="utf-8")
    manifest = synthetic_manifest(["provider", "consumer"])
    before_state = build_cluster_dependency_state(
        {"provider": empty, "consumer": tmp_path / "empty-consumer"},
        state_kind="before",
        source_kind="working_tree",
        manifest=manifest,
        service_meta={"provider": {"exact": True, "empty": True, "state_source": "repository_absent"}, "consumer": {"exact": True, "empty": True, "state_source": "repository_absent"}},
    )
    (tmp_path / "empty-consumer").mkdir()
    after_state = build_cluster_dependency_state(
        {"provider": empty, "consumer": root},
        state_kind="after",
        source_kind="working_tree",
        manifest=manifest,
    )
    result = analyze_interdependencies(
        [{"service_id": "provider", "changed": True}, {"service_id": "consumer", "changed": True}],
        before_state=before_state,
        after_state=after_state,
        comparison_mode="exact_boundary_snapshots",
    )
    assert any(item["classification"] == "broken" and item["contract_id"] == "POST /v1/test" for item in result["items"])
    assert not any(item["classification"] == "introduced" for item in result["items"])


def test_breaking_modification_is_not_generic_modified() -> None:
    fact = {
        "provider_service": "provider",
        "consumer_service": "consumer",
        "relationship_type": "http_contract",
        "contract_identity": "POST /v1/test",
        "provider_present": True,
        "consumer_reference_present": True,
        "confirmed": True,
        "provenance": "source_extraction",
    }
    state = {"relationships": [fact], "provider_contracts": [{"service_id": "provider", "contract_identity": "POST /v1/test"}], "services": {}}
    broken = analyze_interdependencies(
        [{"service_id": "provider", "changed": True, "contract_changes": [{"kind": "required_request_field_added", "method": "POST", "path": "/v1/test", "breaking": True, "evidence_ids": ["EV-021"], "review_id": "rev-p"}]}],
        before_state=state,
        after_state=state,
        comparison_mode="exact_boundary_snapshots",
    )
    assert broken["items"][0]["classification"] == "broken"
    safe = analyze_interdependencies(
        [{"service_id": "provider", "changed": True, "contract_changes": [{"kind": "handler_changed", "method": "POST", "path": "/v1/test", "breaking": False, "evidence_ids": ["EV-4"]}]}],
        before_state=state,
        after_state=state,
        comparison_mode="exact_boundary_snapshots",
    )
    assert safe["items"][0]["classification"] == "modified"


def test_partial_coverage_names_the_missing_service(tmp_path: Path) -> None:
    provider = tmp_path / "provider"
    _init(provider)
    _write(provider, "src/server.js", PROVIDER_V1)
    _commit(provider, "baseline", "2026-10-01T12:00:00")
    result = _run(
        tmp_path,
        {"kind": "working_tree"},
        [
            {"service_id": "provider", "repository": "provider", "repository_path": str(provider)},
            {"service_id": "missing", "repository": "missing", "repository_path": str(tmp_path / "missing")},
        ],
    )
    analysis = result["interdependencies"]
    assert analysis["comparison_mode"] == "partial"
    assert "missing" in analysis["comparison_coverage"]["services_partial"]
    assert "Exact boundary dependency comparison" not in render_markdown({"interdependencies": analysis, "assessment": {}, "source": {}, "counters": {}, "repositories": []})


def test_child_evidence_ids_stay_qualified() -> None:
    result = analyze_interdependencies(
        [
            {"service_id": "provider", "changed": True, "review_id": "review-a", "decision": "block", "reason_codes": ["BG-VERIFY-001"], "contract_changes": [{"kind": "operation_removed", "method": "POST", "path": "/v1/test", "breaking": True, "evidence_ids": ["EV-021"], "consumers": ["consumer"]}]},
            {"service_id": "consumer", "changed": True, "review_id": "review-b", "contract_changes": [{"kind": "consumption_removed", "role": "consumer", "provider": "provider", "method": "POST", "path": "/v1/test", "evidence_ids": ["EV-021"]}]},
        ],
        [{"from_service": "consumer", "to_service": "provider", "kind": "http", "method": "POST", "path": "/v1/test", "provenance": "confirmed"}],
    )
    item = next(row for row in result["items"] if row["contract_id"] == "POST /v1/test" and row["classification"] != "unchanged")
    pairs = {(ref["review_id"], ref["evidence_id"]) for ref in item["child_evidence_refs"]}
    assert ("review-a", "EV-021") in pairs
    assert ("review-b", "EV-021") in pairs
    assert item["classification"] != "resolved_by_change_set"


def test_resolved_by_change_set_keeps_the_child_block(tmp_path: Path) -> None:
    provider = tmp_path / "provider"
    consumer = tmp_path / "consumer"
    for repo, filename, first, second, when in (
        (provider, "src/server.js", PROVIDER_V1, PROVIDER_V2, "2026-11-10T12:00:00"),
        (consumer, "src/client.js", CONSUMER_V1, CONSUMER_V2, "2026-11-11T12:00:00"),
    ):
        _init(repo)
        _write(repo, filename, first)
        _commit(repo, "baseline", "2026-10-01T12:00:00")
        _write(repo, filename, second)
        _commit(repo, "migrate", when)
    result = _run(
        tmp_path,
        {"kind": "timeframe", "since": "2026-11-01", "until": "2026-11-21"},
        [
            {"service_id": "provider", "repository": "provider", "repository_path": str(provider)},
            {"service_id": "consumer", "repository": "consumer", "repository_path": str(consumer)},
        ],
    )
    provider_row = next(item for item in result["repositories"] if item["service_id"] == "provider")
    assert provider_row["decision"] == "block"
    assert any(item["classification"] == "resolved_by_change_set" for item in result["interdependencies"]["items"])
    assert not any(item["classification"] == "broken" for item in result["interdependencies"]["items"])


def test_cancel_during_state_build(tmp_path: Path) -> None:
    provider = tmp_path / "provider"
    _init(provider)
    _write(provider, "src/server.js", PROVIDER_V1)
    _commit(provider, "baseline", "2026-10-01T12:00:00")
    calls = {"count": 0}

    def cancel() -> bool:
        calls["count"] += 1
        return calls["count"] > 2

    built = build_review_boundaries(
        [{"service_id": "provider", "repository": "provider", "repository_path": str(provider)}],
        {"kind": "working_tree"},
        settings=_settings(tmp_path),
        work_dir=tmp_path / "work",
        cancel=cancel,
    )
    assert built["cancelled"] is True
