"""Regression tests for the audit remediation. These cover semantics the older suite missed."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from dep_intel.api import app
from dep_intel.app_state import build_app_state
from dep_intel.config import Settings
from dep_intel.cross_repo_graph import index_snapshot
from dep_intel.graph_models import CONSUMES_HTTP, HTTP_CONTRACT, GraphEdge, GraphNode, Partition, symbol_key
from dep_intel.path_hygiene import scrub_text
from dep_intel.reporting.builder import generate_report
from dep_intel.reporting.collect import history_for, relationships
from dep_intel.reporting.render_markdown import render_markdown
from dep_intel.reporting.render_pdf import render_report_pdf
from dep_intel.reporting.review_summary import summarize_review
from dep_intel.reporting.store import list_reports, read_manifest, read_report
from dep_intel.review_export import ensure_export
from dep_intel.service_clusters import cluster_id_for, detect_service_clusters
from dep_intel.store import write_json

from test_intelligence_reports import _settings, _snapshot


def test_stored_report_does_not_change_when_a_later_review_is_added(tmp_path: Path, monkeypatch) -> None:
    settings = _settings(tmp_path)
    snapshot = _snapshot()
    document = generate_report(settings, snapshot, "workspace")
    stored = json.loads((tmp_path / "reports" / document["report_id"] / "report.json").read_text(encoding="utf-8"))
    before = json.dumps(stored["review_activity"], sort_keys=True)
    _write_review(tmp_path, "later", "warn", "auth-service")
    monkeypatch.setattr("dep_intel.api.Settings.load", lambda *_args, **_kwargs: settings)
    monkeypatch.setattr("dep_intel.api.get_engine", lambda _settings=None: type("Engine", (), {"current": lambda self: snapshot, "summary": lambda self: {"version": "g"}})())
    client = TestClient(app)
    opened = client.get(f"/api/reports/{document['report_id']}")
    assert opened.status_code == 200
    body = opened.json()
    assert body["reproducibility"]["report_hash"] == stored["reproducibility"]["report_hash"]
    assert json.dumps(body["review_activity"], sort_keys=True) == before
    for kind in ("json", "markdown", "pdf"):
        exported = client.get(f"/api/reports/{document['report_id']}/export/{kind}")
        assert exported.status_code == 200
    exported_json = client.get(f"/api/reports/{document['report_id']}/export/json").json()
    assert exported_json["review_activity"]["counts"] == stored["review_activity"]["counts"]
    assert exported_json["reproducibility"]["report_hash"] == stored["reproducibility"]["report_hash"]


def test_zero_review_report_stays_empty(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    document = generate_report(settings, _snapshot(), "workspace")
    assert document["review_activity"]["total_considered"] == 0
    _write_review(tmp_path, "later", "warn", "auth-service")
    stored = read_report(tmp_path, document["report_id"])
    assert stored["review_activity"]["counts"] == {"pass": 0, "warn": 0, "block": 0, "incomplete": 0}


def test_service_cluster_reviews_match_the_canonical_slug(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    snapshot = _chain_snapshot()
    snapshot.partitions["auth-service"].repository = "https://github.com/hvrcorp/uocp-api-gateway"
    write_json(tmp_path, "repo_graphs.json", {"repos": [{
        "slug": "hvrcorp_uocp_api_gateway",
        "name": "hvrcorp/uocp-api-gateway",
        "url": "https://github.com/hvrcorp/uocp-api-gateway",
        "checkout": str(tmp_path / "checkouts" / "hvrcorp_uocp_api_gateway"),
    }]})
    _write_review(tmp_path, "gateway-review", "warn", "hvrcorp_uocp_api_gateway")
    _write_review(tmp_path, "tooling-review", "warn", "tooling-repo")
    groups = detect_service_clusters(snapshot)
    cluster = generate_report(settings, snapshot, "service_cluster", cluster_id=groups["clusters"][0]["cluster_id"])
    ids = [item["review_id"] for item in cluster["review_activity"]["attention_reviews"]]
    assert "gateway-review" in ids
    assert "tooling-review" not in ids
    repository = generate_report(settings, snapshot, "repository", "auth-service")
    assert [item["review_id"] for item in repository["review_activity"]["attention_reviews"]] == ["gateway-review"]


def test_legacy_cluster_request_is_a_workspace_report(tmp_path: Path) -> None:
    document = generate_report(_settings(tmp_path), _snapshot(), "cluster")
    assert document["scope"] == "workspace"
    assert document["requested_scope"] == "cluster"
    assert "service_clusters" in document
    assert "standalone" in document


def test_service_cluster_is_not_the_workspace(tmp_path: Path) -> None:
    snapshot = _chain_snapshot()
    settings = _settings(tmp_path)
    groups = detect_service_clusters(snapshot)
    assert groups["confirmed_calls"] == 2
    assert len(groups["clusters"]) == 1
    assert groups["clusters"][0]["services"] == ["auth-service", "reporting-service", "user-service"]
    assert [item["service_id"] for item in groups["standalone"]] == ["tooling-repo"]
    workspace = generate_report(settings, snapshot, "workspace")
    cluster = generate_report(settings, snapshot, "service_cluster", cluster_id=groups["clusters"][0]["cluster_id"])
    repository = generate_report(settings, snapshot, "repository", "tooling-repo")
    assert workspace["workspace"]["service_count"] == 4
    assert cluster["scope"] == "service_cluster"
    assert cluster["workspace"]["service_count"] == 3
    assert cluster["identity"]["cluster_id"] == groups["clusters"][0]["cluster_id"]
    assert "tooling-repo" not in cluster["identity"]["services"]
    assert repository["scope"] == "repository"
    assert repository["identity"]["service"] == "tooling-repo"


def test_cluster_id_is_stable_and_ignores_edge_order() -> None:
    services = ["user-service", "auth-service", "reporting-service"]
    assert cluster_id_for(services) == cluster_id_for(list(reversed(services)))
    assert cluster_id_for(services) != cluster_id_for(["auth-service", "user-service"])
    forward = detect_service_clusters(_chain_snapshot())
    backward = detect_service_clusters(_chain_snapshot(reverse=True))
    assert forward["clusters"][0]["cluster_id"] == backward["clusters"][0]["cluster_id"]


def test_code_symbols_are_not_unresolved_providers(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    write_json(tmp_path / "topology", "effective.json", {
        "relationships": [],
        "unresolved": [
            {"from": "checkout-service", "method": "GET", "path": "/api/pay", "host": "payment.internal"},
            {"from": "checkout-service", "method": "GET", "path": "/api/pay", "host": "payment.internal"},
        ],
    })
    document = generate_report(settings, _snapshot(), "workspace")
    discovery = document["provider_discovery"]
    assert discovery["unresolved_observation_count"] == 2
    assert discovery["unique_unresolved_reference_count"] == 1
    assert document["code_graph_diagnostics"]["unresolved_symbol_count"] >= 1
    blob = json.dumps(document["recommendations"])
    assert "append" not in blob
    assert "AVAudioPlayer" not in blob
    assert "payment.internal" in blob


def test_history_uses_the_canonical_graph_slug(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    write_json(tmp_path, "repo_graphs.json", {"repos": [{
        "slug": "hvrcorp_uocp_api_gateway",
        "name": "hvrcorp/uocp-api-gateway",
        "service_id": "uocp-api-gateway",
        "repository": "https://github.com/hvrcorp/uocp-api-gateway",
        "checkout": str(tmp_path / "checkouts" / "hvrcorp_uocp_api_gateway"),
    }]})
    write_json(tmp_path / "graphs" / "hvrcorp_uocp_api_gateway", "cochange.json", {"commit_count": 4, "coverage": 1})
    history = history_for(settings, "https://github.com/hvrcorp/uocp-api-gateway", service_id="uocp-api-gateway")
    assert history["status"] == "available"
    assert history["slug"] == "hvrcorp_uocp_api_gateway"
    (tmp_path / "graphs" / "hvrcorp_uocp_api_gateway" / "cochange.json").unlink()
    missing = history_for(settings, "https://github.com/hvrcorp/uocp-api-gateway", service_id="uocp-api-gateway")
    assert missing["status"] == "unavailable"


def test_workspace_history_agrees_with_app_state(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    write_json(tmp_path, "repo_graphs.json", {"repos": [
        {"slug": "auth-service", "name": "auth-service", "service_id": "auth-service", "repository": "auth-service"},
        {"slug": "user-service", "name": "user-service", "service_id": "user-service", "repository": "user-service"},
    ]})
    write_json(tmp_path / "graphs" / "auth-service", "cochange.json", {"commit_count": 2, "coverage": 1})
    write_json(tmp_path / "graphs" / "user-service", "cochange.json", {"commit_count": 3, "coverage": 1})
    state = build_app_state(settings, {"version": "g", "services": [], "contract_count": 0, "unresolved_count": 9}, False)
    document = generate_report(settings, _snapshot(), "workspace")
    assert state["intelligence"]["history"]["ready_services"] == 2
    assert document["history"]["ready"] == 2
    assert document["history"]["missing"] == []
    assert document["code_graph_diagnostics"]["unresolved_symbol_count"] == 1
    assert document["provider_discovery"]["status"] == "not_scanned"


def test_attention_review_keeps_rule_and_file(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _write_review(tmp_path, "rev-temporal", "warn", "user-service", extra={
        "gate": {
            "decision": "warn",
            "reason_codes": ["temporal_only_review_target"],
            "decision_rules": [{"rule_id": "BG-TEMPORAL-001", "title": "Historical association is not a blocker", "decision": "warn", "outcome": "triggered"}],
        },
        "findings": [
            {"service_id": "user-service", "file_path": "app/main.py", "claim": "Historical association."},
            {"service_id": "user-service", "file_path": "app/main.py", "claim": "Same file again."},
            {"service_id": "user-service", "file_path": "app/providers.py", "claim": "Another file."},
        ],
        "evidence_catalog": [{"source": "git_history", "statement": "Repository history identified the files as temporal review targets.", "file_path": "app/main.py"}],
        "commentary": {"why_it_matters": "", "next_action": ""},
    })
    document = generate_report(settings, _snapshot(), "repository", "user-service")
    card = document["review_activity"]["attention_reviews"][0]
    assert card["evidence_summary"]["origin"] in {"git_history", "NRM", "temporal_only"}
    assert "historical" in card["evidence_summary"]["statement"].lower() or "repository history" in card["evidence_summary"]["statement"].lower()
    assert card["primary_rule"]["rule_id"] == "BG-TEMPORAL-001"
    assert card["primary_rule"]["title"] == "Historical association is not a blocker"
    assert "temporal_only_review_target" in card["reason_codes"]
    assert card["targets"]["unique_file_count"] == 2
    assert card["targets"]["total_findings"] == 3
    assert "app/main.py" in json.dumps(card["targets"])
    assert card["why_it_matters"]
    assert card["next_action"]
    assert "this evidence alone does not confirm a current structural dependency" in card["why_it_matters"].lower()
    text = render_markdown(document)
    assert "### PASS" not in text
    assert "BG-TEMPORAL-001" in text
    assert "app/main.py" in text


def test_temporal_card_prefers_history_and_recovers_a_missing_rule(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _write_review(tmp_path, "rev-old-temporal", "warn", "user-service", extra={
        "gate": {"decision": "warn", "reason_codes": ["temporal_only_review_target"]},
        "findings": [{"service_id": "user-service", "file_path": "app/main.py", "claim": "Historical association."}],
        "evidence_catalog": [
            {"source": "cluster_graph", "statement": "The published graph matches this revision."},
            {"source": "NRM", "statement": "Historically associated with the changed file, but not structurally reachable in the current graph."},
        ],
        "commentary": {},
    })
    document = generate_report(settings, _snapshot(), "repository", "user-service")
    card = document["review_activity"]["attention_reviews"][0]
    assert card["primary_rule"]["rule_id"] == "BG-TEMPORAL-001"
    assert card["primary_rule"]["title"] == "Historical association is not a blocker"
    assert card["evidence_summary"]["origin"] == "NRM"
    assert "published graph matches" not in card["evidence_summary"]["statement"].lower()


def test_incomplete_summary_is_never_empty(tmp_path: Path) -> None:
    row = {"review_id": "rev-incomplete", "decision": "incomplete", "service": "auth-service", "repository": "auth-service", "completed_at": ""}
    directory = tmp_path / "reviews" / "rev-incomplete"
    directory.mkdir(parents=True)
    (directory / "report.json").write_text(json.dumps({
        "review_id": "rev-incomplete",
        "gate": {"decision": "incomplete", "decision_rules": [{"rule_id": "BG-GRAPH-001", "title": "Required structural evidence unavailable", "decision": "incomplete", "outcome": "triggered"}]},
        "commentary": {"why_it_matters": "", "next_action": ""},
    }), encoding="utf-8")
    (directory / "manifest.json").write_text("{}", encoding="utf-8")
    card = summarize_review(tmp_path, row)
    assert card["why_it_matters"]
    assert card["next_action"]
    assert "Not available" in card["validation"]


def test_review_counts_include_reviews_omitted_from_the_cards(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    for index in range(12):
        _write_review(tmp_path, f"warn-{index}", "warn", "auth-service")
    for index in range(4):
        _write_review(tmp_path, f"pass-{index}", "pass", "auth-service")
    document = generate_report(settings, _snapshot(), "repository", "auth-service")
    activity = document["review_activity"]
    assert activity["total_considered"] == 16
    assert activity["counts"]["warn"] == 12
    assert activity["counts"]["pass"] == 4
    assert len(activity["attention_reviews"]) == 12
    assert all(item["decision"] != "pass" for item in activity["attention_reviews"])
    assert "### PASS" not in render_markdown(document)


def test_legacy_validation_is_distinct_from_a_missing_record(tmp_path: Path) -> None:
    legacy = summarize_review(tmp_path, {"review_id": "missing", "decision": "warn", "service": "auth-service"})
    directory = tmp_path / "reviews" / "empty"
    directory.mkdir(parents=True)
    (directory / "report.json").write_text(json.dumps({"review_id": "empty", "gate": {"decision": "warn"}, "gate_validation": {}}), encoding="utf-8")
    (directory / "manifest.json").write_text("{}", encoding="utf-8")
    broken = summarize_review(tmp_path, {"review_id": "empty", "decision": "warn", "service": "auth-service"})
    assert "before a stored independent validation result" in legacy["validation"]
    assert "missing or incomplete" in broken["validation"]
    assert legacy["validation"] != broken["validation"]


def test_declared_exact_keeps_provenance_and_resolution(tmp_path: Path) -> None:
    snapshot = _declared_snapshot()
    payload = relationships(snapshot)
    row = payload["relationships"][0]
    assert row["provenance"] == "declared"
    assert row["resolution"] == "exact"
    assert sum(payload["provenance"].values()) == payload["total"]


def test_relationship_aggregates_use_every_edge(tmp_path: Path) -> None:
    snapshot = _many_edges(201)
    payload = relationships(snapshot)
    document = generate_report(_settings(tmp_path), snapshot, "workspace")
    assert payload["total"] == 201
    assert sum(payload["provenance"].values()) == 201
    assert document["relationships"]["total"] == 201
    assert document["relationships"]["included"] == 100
    assert document["concentration"]["widest_contract"]["consumers"] == 2
    services = document["service_clusters"]["items"][0]["services"]
    assert "zzz-late" in services


def test_repository_health_does_not_copy_another_repository(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    write_json(tmp_path, "dep_matrix.json", {"repositories": {
        "acme/auth-service": {"packages": [{"name": "a"}, {"name": "b"}]},
        "acme/user-service": {"packages": [{"name": "only-user"}]},
    }})
    auth = generate_report(settings, _snapshot(), "repository", "auth-service")
    user = generate_report(settings, _snapshot(), "repository", "user-service")
    assert auth["health"]["packages"]["finding_count"] == 2
    assert user["health"]["packages"]["finding_count"] == 1
    assert "only-user" not in json.dumps(auth["health"])


def test_absolute_paths_are_removed_from_review_exports(tmp_path: Path) -> None:
    review_id = "rev-path"
    directory = tmp_path / "reviews" / review_id
    directory.mkdir(parents=True)
    report = {
        "review_id": review_id,
        "change": {"repository": "/home/example/project/output/checkouts/auth-service", "base_sha": "a", "head_sha": "b"},
        "gate": {"decision": "warn"},
        "diagnostics": [{"message": r"C:\Users\Example\project\output\checkouts\auth-service\src\server.py"}],
    }
    (directory / "report.json").write_text(json.dumps(report), encoding="utf-8")
    (directory / "manifest.json").write_text(json.dumps({"review_id": review_id}), encoding="utf-8")
    (directory / "feedback.json").write_text("{}", encoding="utf-8")
    json_path = ensure_export(tmp_path, review_id, "json")
    markdown_path = ensure_export(tmp_path, review_id, "markdown")
    pdf_path = ensure_export(tmp_path, review_id, "pdf")
    zip_path = ensure_export(tmp_path, review_id, "zip")
    json_text = json_path.read_text(encoding="utf-8")
    markdown_text = markdown_path.read_text(encoding="utf-8")
    pdf_text = pdf_path.read_bytes().decode("latin1", errors="ignore")
    archive = zipfile.ZipFile(io.BytesIO(zip_path.read_bytes()))
    zipped = "\n".join(archive.read(name).decode("latin1", errors="ignore") for name in archive.namelist())
    for text in (json_text, markdown_text, pdf_text, zipped):
        assert "/home/example" not in text
        assert "C:\\Users\\Example" not in text
    assert scrub_text("/home/example/project/output/checkouts/auth-service/src/server.py") == "auth-service/src/server.py"
    assert "app/main.py" == scrub_text("app/main.py")


def test_human_exports_distinguish_workspace_and_service_cluster(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    snapshot = _chain_snapshot()
    groups = detect_service_clusters(snapshot)
    workspace = generate_report(settings, snapshot, "workspace")
    cluster = generate_report(settings, snapshot, "service_cluster", cluster_id=groups["clusters"][0]["cluster_id"])
    label = groups["clusters"][0]["label"]
    workspace_md = render_markdown(workspace)
    workspace_pdf = render_report_pdf(workspace)
    cluster_md = render_markdown(cluster)
    cluster_pdf = render_report_pdf(cluster)
    for text in (workspace_md, workspace_pdf.decode("latin-1")):
        assert "Detected service clusters" in text
        assert "Standalone repositories" in text
        assert label in text
        assert "tooling-repo" in text
        assert "auth-service" in text
    assert "Workspace overview" in workspace_md
    assert b"Workspace overview" in workspace_pdf
    for text in (cluster_md, cluster_pdf.decode("latin-1")):
        assert "Service cluster" in text
        assert label in text
        assert groups["clusters"][0]["cluster_id"] in text
        assert "Cluster membership" in text
        assert "auth-service" in text
        assert "user-service" in text
        assert "reporting-service" in text
    assert "Workspace overview" not in cluster_md
    assert b"Workspace overview" not in cluster_pdf
    assert "tooling-repo" not in cluster_md
    assert "tooling-repo" not in cluster_pdf.decode("latin-1")
    membership = cluster_md.split("## Cluster membership", 1)[1].split("##", 1)[0]
    assert "tooling-repo" not in membership
    assert "intelligence completeness" in cluster_md
    assert b"intelligence completeness" in cluster_pdf
    assert workspace["review_activity"]["summary_version"] == 2
    assert cluster["review_activity"]["summary_version"] == 2


def test_cross_cluster_review_target_stays_outside_membership(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    snapshot = _two_cluster_snapshot()
    groups = detect_service_clusters(snapshot)
    alpha = next(item for item in groups["clusters"] if "alpha-auth" in item["services"])
    _write_review(tmp_path, "outside", "warn", "alpha-auth", extra={
        "impact": {"affected_services": ["beta-billing"]},
        "gate": {"decision": "warn", "reason_codes": ["review_target"]},
    })
    document = generate_report(settings, snapshot, "service_cluster", cluster_id=alpha["cluster_id"])
    card = document["review_activity"]["attention_reviews"][0]
    assert card["cross_cluster_targets"] == ["beta-billing"]
    assert document["identity"]["services"] == ["alpha-auth", "alpha-user"]
    text = render_markdown(document)
    pdf = render_report_pdf(document).decode("latin-1")
    membership = text.split("## Cluster membership", 1)[1].split("##", 1)[0]
    assert "beta-billing" not in membership
    assert "External review targets" in text
    assert "beta-billing" in text
    assert "External review targets" in pdf
    assert "beta-billing" in pdf
    assert document["review_activity"]["counts"]["warn"] == 1


def test_member_alias_is_not_called_an_external_target(tmp_path: Path) -> None:
    _write_review(tmp_path, "alias", "warn", "alpha-auth", extra={
        "impact": {"affected_services": ["alpha-auth", "graph-slug-auth", "beta-billing"]},
        "gate": {"decision": "warn", "reason_codes": ["review_target"]},
    })
    card = summarize_review(
        tmp_path,
        {"review_id": "alias", "decision": "warn", "service": "alpha-auth", "repository": "alpha-auth"},
        members={"alpha-auth", "alpha-user"},
        known={"alpha-auth", "alpha-user", "graph-slug-auth"},
    )
    assert card["cross_cluster_targets"] == ["beta-billing"]


def test_temporal_evidence_is_not_labelled_as_nami_rank(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _write_review(tmp_path, "history", "warn", "user-service", extra={
        "gate": {
            "decision": "warn",
            "reason_codes": ["temporal_only_review_target"],
            "decision_rules": [{"rule_id": "BG-TEMPORAL-001", "title": "Historical association is not a blocker", "decision": "warn", "outcome": "triggered"}],
        },
        "findings": [{"service_id": "user-service", "file_path": "app/main.py", "claim": "Historical association."}],
        "evidence_catalog": [
            {"source": "git_history", "statement": "Repository history identified app/main.py."},
            {"source": "NRM", "statement": "Nami Rank prioritized app/main.py."},
        ],
        "commentary": {},
    })
    history = generate_report(settings, _snapshot(), "repository", "user-service")
    card = history["review_activity"]["attention_reviews"][0]
    assert card["evidence_summary"]["source"] == "Repository history"
    assert card["evidence_summary"]["origin"] == "git_history"
    assert card["ranking_context"]["source"] == "Nami Rank"
    text = render_markdown(history)
    assert "Evidence: Repository history" in text
    assert "Ranking context: Nami Rank" in text
    assert "Evidence: Nami Rank" not in text
    assert "does not confirm a current structural dependency" in text
    assert "not a blocker" in text.lower()


def test_nrm_only_temporal_evidence_stays_temporal(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _write_review(tmp_path, "ranked", "warn", "user-service", extra={
        "gate": {"decision": "warn", "reason_codes": ["temporal_only_review_target"]},
        "findings": [{"service_id": "user-service", "file_path": "app/main.py"}],
        "evidence_catalog": [{"source": "NRM", "statement": "Historically associated with the changed file, but not structurally reachable in the current graph."}],
        "commentary": {},
    })
    document = generate_report(settings, _snapshot(), "repository", "user-service")
    card = document["review_activity"]["attention_reviews"][0]
    assert card["evidence_summary"]["source"] == "Temporal association evidence"
    assert card["evidence_summary"]["origin"] == "NRM"
    assert card["ranking_context"]["source"] == "Nami Rank"
    text = render_markdown(document)
    assert "Evidence: Temporal association evidence" in text
    assert "Evidence: Nami Rank" not in text
    assert "Ranking context: Nami Rank" in text
    assert "does not confirm a current structural dependency" in text


def test_recommendation_title_is_printed_once(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    snapshot = _snapshot()
    snapshot.partitions["auth-service"].quality = "stale"
    document = generate_report(settings, snapshot, "repository", "auth-service")
    title = "Refresh auth-service intelligence"
    assert any(item["title"] == title for item in document["recommendations"])
    markdown = render_markdown(document)
    pdf = render_report_pdf(document).decode("latin-1")
    assert markdown.count(title) == 1
    assert pdf.count(title) == 1
    assert "## Review activity" in markdown
    assert "## Recent reviews" not in markdown
    assert b"Review activity" in render_report_pdf(document)
    assert b"Recent reviews" not in render_report_pdf(document)


def test_validation_label_is_not_repeated(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _write_review(tmp_path, "checked", "warn", "user-service", extra={"gate_validation": {"status": "validated"}})
    document = generate_report(settings, _snapshot(), "repository", "user-service")
    text = render_markdown(document)
    assert "Decision logic: Validated" in text
    assert "Decision logic: Decision logic validated" not in text


def test_manifest_keeps_service_and_cluster_identity_apart(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    snapshot = _chain_snapshot()
    groups = detect_service_clusters(snapshot)
    workspace = generate_report(settings, snapshot, "workspace")
    cluster = generate_report(settings, snapshot, "service_cluster", cluster_id=groups["clusters"][0]["cluster_id"])
    repository = generate_report(settings, snapshot, "repository", "tooling-repo")
    workspace_manifest = read_manifest(tmp_path, workspace["report_id"])
    cluster_manifest = read_manifest(tmp_path, cluster["report_id"])
    repository_manifest = read_manifest(tmp_path, repository["report_id"])
    assert workspace_manifest["service_id"] is None
    assert workspace_manifest["cluster_id"] is None
    assert cluster_manifest["service_id"] is None
    assert cluster_manifest["cluster_id"] == groups["clusters"][0]["cluster_id"]
    assert repository_manifest["service_id"] == "tooling-repo"
    assert repository_manifest["cluster_id"] is None
    (tmp_path / "reports" / "rpt-oldmanifest").mkdir(parents=True)
    (tmp_path / "reports" / "rpt-oldmanifest" / "manifest.json").write_text(json.dumps({
        "report_id": "rpt-oldmanifest",
        "scope": "cluster",
        "service_id": None,
        "graph_version": "g",
        "created_at": "2026-01-01T00:00:00+00:00",
    }), encoding="utf-8")
    listed = list_reports(tmp_path, limit=20)
    old = next(item for item in listed if item["report_id"] == "rpt-oldmanifest")
    assert old["cluster_id"] is None
    assert old["scope"] == "cluster"


def test_old_report_still_renders_without_new_fields() -> None:
    document = {
        "schema": "nami.intelligence.report",
        "scope": "cluster",
        "report_id": "rpt-old",
        "generated_at": "2026-01-01T00:00:00+00:00",
        "executive_summary": {"headline": "Old workspace", "summary": "Stored.", "readiness_label": "Partial"},
        "intelligence_status": {},
        "recommendations": [{"title": "Refresh auth-service intelligence", "reason": "Stored."}],
        "identity": {},
        "reproducibility": {},
        "review_activity": {"counts": {"pass": 1, "warn": 0, "block": 0, "incomplete": 0}, "reviews": [{"decision": "warn", "headline": "Look", "validation": "Decision logic validation: Not available"}]},
    }
    text = render_markdown(document)
    pdf = render_report_pdf(document)
    assert "Workspace overview" in text
    assert "Review activity" in text
    assert "Decision logic: Decision logic validation: Not available" not in text
    assert pdf.startswith(b"%PDF")
    assert document.get("review_activity", {}).get("summary_version") is None


def test_new_report_without_a_gate_rule_is_not_legacy_by_missing_rule(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _write_review(tmp_path, "plain", "warn", "user-service", extra={"gate": {"decision": "warn", "reason_codes": ["review_target"]}})
    document = generate_report(settings, _snapshot(), "repository", "user-service")
    card = document["review_activity"]["attention_reviews"][0]
    assert card["primary_rule"] is None
    assert document["review_activity"]["summary_version"] == 2


def _write_review(root: Path, review_id: str, decision: str, service: str, extra: dict | None = None) -> None:
    payload = {
        "review_id": review_id,
        "gate": {"decision": decision},
        "change": {"service_id": service, "repository": service},
        "commentary": {"summary": f"{decision} summary", "why_it_matters": "Stored why.", "next_action": "Stored action."},
        "gate_validation": {"status": "validated"},
    }
    if extra:
        payload.update(extra)
        if "gate" in extra:
            payload["gate"] = extra["gate"]
    directory = root / "reviews" / review_id
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "report.json").write_text(json.dumps(payload), encoding="utf-8")
    (directory / "manifest.json").write_text(json.dumps({"review_id": review_id, "completed_at": "2026-10-31T00:00:00+00:00"}), encoding="utf-8")


def _chain_snapshot(reverse: bool = False):
    services = ["auth-service", "user-service", "reporting-service", "tooling-repo"]
    partitions = {}
    nodes = []
    edges = []
    contracts = {}
    for service in services:
        part = Partition(service_id=service, repository=service, workspace_head=f"head-{service}", quality="ok")
        partitions[service] = part
    pairs = [("auth-service", "user-service"), ("user-service", "reporting-service")]
    if reverse:
        pairs = list(reversed(pairs))
    for source, target in pairs:
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
        nodes.extend([contract, caller])
        edges.append(GraphEdge(caller.key, contract.key, CONSUMES_HTTP, {"status": "declared", "resolution": "exact"}))
        contracts[target] = contract
    return index_snapshot(
        version="g-chain",
        generated_at="2026-01-01T00:00:00+00:00",
        partitions=partitions,
        overlay={},
        bridges=edges,
        diagnostics=[],
        fingerprints=[],
    )


def _two_cluster_snapshot():
    pairs = [("alpha-auth", "alpha-user"), ("beta-billing", "beta-pay")]
    partitions = {}
    edges = []
    services = ["alpha-auth", "alpha-user", "beta-billing", "beta-pay"]
    for service in services:
        partitions[service] = Partition(service_id=service, repository=service, workspace_head=f"head-{service}", quality="ok")
    for source, target in pairs:
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
        version="g-two",
        generated_at="2026-01-01T00:00:00+00:00",
        partitions=partitions,
        overlay={},
        bridges=edges,
        diagnostics=[],
        fingerprints=[],
    )


def _declared_snapshot():
    identity = "http::GET:/health"
    contract = GraphNode(
        key=symbol_key("auth-service", "", identity, HTTP_CONTRACT),
        category=HTTP_CONTRACT,
        service_id="auth-service",
        name="GET /health",
        qualified_name=identity,
        file_path="src/app.py",
        kind=HTTP_CONTRACT,
        metadata={"identity": identity},
    )
    caller = GraphNode(
        key=symbol_key("user-service", "src/app.py", "user-service::call", "Function"),
        category="code_symbol",
        service_id="user-service",
        name="call",
        qualified_name="user-service::call",
        file_path="src/app.py",
        kind="Function",
    )
    auth = Partition(service_id="auth-service", repository="auth-service", workspace_head="a", quality="ok")
    user = Partition(service_id="user-service", repository="user-service", workspace_head="b", quality="ok")
    auth.nodes[contract.key] = contract
    user.nodes[caller.key] = caller
    return index_snapshot(
        version="g",
        generated_at="2026-01-01T00:00:00+00:00",
        partitions={"auth-service": auth, "user-service": user},
        overlay={},
        bridges=[GraphEdge(caller.key, contract.key, CONSUMES_HTTP, {"status": "declared", "resolution": "exact"})],
        diagnostics=[],
        fingerprints=[],
    )


def _many_edges(count: int):
    hub = Partition(service_id="hub", repository="hub", workspace_head="h", quality="ok")
    partitions = {"hub": hub}
    edges = []
    shared = _contract_node("hub", "shared")
    hub.nodes[shared.key] = shared
    consumers = [f"c{index:03d}" for index in range(count - 1)] + ["zzz-late"]
    for index, service in enumerate(consumers):
        part = Partition(service_id=service, repository=service, workspace_head="c", quality="ok")
        caller = GraphNode(
            key=symbol_key(service, "src/app.py", f"{service}::call", "Function"),
            category="code_symbol",
            service_id=service,
            name="call",
            qualified_name=f"{service}::call",
            file_path="src/app.py",
            kind="Function",
        )
        part.nodes[caller.key] = caller
        partitions[service] = part
        if service in {"c000", "zzz-late"}:
            contract = shared
        else:
            contract = _contract_node("hub", service)
            hub.nodes[contract.key] = contract
        edges.append(GraphEdge(caller.key, contract.key, CONSUMES_HTTP, {"status": "extracted", "resolution": "exact"}))
    return index_snapshot(
        version="g-many",
        generated_at="2026-01-01T00:00:00+00:00",
        partitions=partitions,
        overlay={},
        bridges=edges,
        diagnostics=[],
        fingerprints=[],
    )


def _contract_node(service: str, name: str) -> GraphNode:
    identity = f"http::GET:/{name}"
    return GraphNode(
        key=symbol_key(service, "", identity, HTTP_CONTRACT),
        category=HTTP_CONTRACT,
        service_id=service,
        name=f"GET /{name}",
        qualified_name=identity,
        file_path="src/app.py",
        kind=HTTP_CONTRACT,
        metadata={"identity": identity},
    )
