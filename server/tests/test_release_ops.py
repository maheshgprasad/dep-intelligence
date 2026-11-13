"""Release-loop helpers stay outside Gate and ranking."""

from pathlib import Path

from dep_intel.graph_models import HTTP_CONTRACT, Partition, GraphNode, symbol_key
from dep_intel.cross_repo_graph import index_snapshot
from dep_intel.release_ops import check_summary, drift_since_last_report, hygiene_rows, match_owner, parse_codeowners
from dep_intel.reporting.store import purge_reports, save_report
from dep_intel.review_store import purge_reviews
from dep_intel.store import write_json
from test_intelligence_reports import _settings


def test_block_fails_the_check_and_warn_stays_green() -> None:
    blocked = check_summary({"review_id": "rev-1", "gate": {"decision": "block"}, "commentary": {"headline": "Contract removed"}, "change": {"service_id": "auth-service", "contract_changes": [{"qualified_name": "POST /v1/validate"}]}, "impact": {"affected_services": ["user-service"]}})
    warned = check_summary({"review_id": "rev-2", "gate": {"decision": "warn"}, "commentary": {"headline": "History only"}})
    assert blocked["exit_code"] == 1
    assert "POST /v1/validate" in blocked["text"]
    assert "user-service" in blocked["text"]
    assert warned["exit_code"] == 0


def test_purge_reports_keeps_reviews_and_does_not_follow_links(tmp_path: Path) -> None:
    report = tmp_path / "reports" / "rpt-one"
    report.mkdir(parents=True)
    (report / "report.json").write_text("{}", encoding="utf-8")
    reviews = tmp_path / "reviews"
    reviews.mkdir()
    (reviews / "keep.txt").write_text("keep", encoding="utf-8")
    (tmp_path / "reports" / "escape").symlink_to(reviews)
    result = purge_reports(tmp_path)
    assert result["removed"] == 2
    assert not report.exists()
    assert (reviews / "keep.txt").read_text(encoding="utf-8") == "keep"


def test_purge_reviews_keeps_reports(tmp_path: Path) -> None:
    review = tmp_path / "reviews" / "rev-1"
    review.mkdir(parents=True)
    (review / "report.json").write_text("{}", encoding="utf-8")
    cache = tmp_path / "reviews" / "_cache"
    cache.mkdir()
    (cache / "pointer.json").write_text("{}", encoding="utf-8")
    report = tmp_path / "reports" / "rpt-one"
    report.mkdir(parents=True)
    (report / "report.json").write_text("{}", encoding="utf-8")
    (tmp_path / "reviews" / "escape").symlink_to(report)
    result = purge_reviews(tmp_path)
    assert result["removed"] == 3
    assert not review.exists()
    assert not cache.exists()
    assert (report / "report.json").read_text(encoding="utf-8") == "{}"


def test_codeowners_last_match_wins() -> None:
    rules = parse_codeowners("* @platform\n/src/users.ts @alice\n# comment\nsrc/generated/ @bots\n")
    assert match_owner("src/users.ts", rules) == "@alice"
    assert match_owner("src/other.ts", rules) == "@platform"
    assert match_owner("src/generated/client.py", rules) == "@bots"


def test_hygiene_is_not_scanned_only_when_the_repository_is_absent(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    write_json(tmp_path, "dep_matrix.json", {"repositories": {"known-repo": {"packages": []}}})
    write_json(tmp_path, "package_updates.json", {
        "updates": {"major": [], "minor": [{"repo": "known-repo", "package": "left-pad", "from": "1.0.0", "to": "1.1.0"}], "patch": []},
    })
    known = Partition(service_id="known", repository="known-repo", workspace_head="a", quality="ok")
    missing = Partition(service_id="missing", repository="missing-repo", workspace_head="b", quality="ok")
    snapshot = index_snapshot(version="g", generated_at="2026-01-01T00:00:00+00:00", partitions={"known": known, "missing": missing}, overlay={}, bridges=[], diagnostics=[], fingerprints=[])
    rows = {item["service"]: item for item in hygiene_rows(settings, snapshot)["rows"]}
    assert rows["known"]["status"] == "minor"
    assert rows["known"]["package"] == "left-pad"
    assert rows["known"]["fixed"] == "1.1.0"
    assert rows["missing"]["status"] == "not_scanned"


def test_drift_names_an_edge_added_since_the_last_report(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    document = {
        "schema": "nami.intelligence.report",
        "report_id": "rpt-old",
        "scope": "workspace",
        "relationships": {"items": [{"from_service": "user-service", "to_service": "auth-service", "contract": "POST /old"}]},
        "services": [],
        "code_graph_diagnostics": {"unresolved_symbol_count": 0},
        "reproducibility": {},
    }
    save_report(tmp_path, document, {"report_id": "rpt-old", "scope": "workspace", "created_at": "2026-01-01T00:00:00+00:00", "graph_version": "g"})
    identity = "http::POST:/new"
    contract = GraphNode(key=symbol_key("auth-service", "", identity, HTTP_CONTRACT), category=HTTP_CONTRACT, service_id="auth-service", name="POST /new", qualified_name=identity, file_path="", kind=HTTP_CONTRACT, metadata={"identity": identity})
    caller_key = symbol_key("user-service", "src/app.py", "call", "Function")
    from dep_intel.graph_models import CONSUMES_HTTP, GraphEdge
    caller = GraphNode(key=caller_key, category="code_symbol", service_id="user-service", name="call", qualified_name="call", file_path="src/app.py", kind="Function")
    auth = Partition(service_id="auth-service", repository="auth-service", workspace_head="a", quality="ok")
    user = Partition(service_id="user-service", repository="user-service", workspace_head="b", quality="stale")
    auth.nodes[contract.key] = contract
    user.nodes[caller.key] = caller
    snapshot = index_snapshot(
        version="g2",
        generated_at="2026-01-02T00:00:00+00:00",
        partitions={"auth-service": auth, "user-service": user},
        overlay={},
        bridges=[GraphEdge(caller.key, contract.key, CONSUMES_HTTP, {"status": "extracted"})],
        diagnostics=[],
        fingerprints=[],
    )
    result = drift_since_last_report(settings, snapshot)
    assert result["status"] == "available"
    assert {"from_service": "user-service", "to_service": "auth-service", "contract": "POST /new"} in result["added"]
    assert result["removed_count"] == 1
    assert "user-service" in result["stale"]
