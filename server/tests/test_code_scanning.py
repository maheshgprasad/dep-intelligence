"""Guards for the CodeQL findings on path access, command arguments, and manifests."""

from __future__ import annotations

from pathlib import Path

from dep_intel.api import review_source_options
from dep_intel.config import Settings
from dep_intel.reporting.store import read_report, save_report
from dep_intel.topology.sources.kubernetes import service_aliases


def test_source_options_rejects_an_arbitrary_directory(tmp_path: Path) -> None:
    secret = tmp_path / "secret-repo"
    secret.mkdir()
    payload = review_source_options(str(secret))
    assert payload["error"] == "repository_not_allowed"
    assert payload["checkout_status"] == "unavailable"
    assert "branches" not in payload


def test_allowlisted_repository_name_resolves_without_using_the_request_as_a_path(tmp_path: Path) -> None:
    from dep_intel.repository_identity import resolve_repository_path

    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    settings = _settings(tmp_path)
    assert resolve_repository_path(settings, "repo") == repo.resolve()
    assert resolve_repository_path(settings, str(outside)) is None
    assert resolve_repository_path(settings, "../outside") is None
    assert resolve_repository_path(settings, str(repo)) is None


def test_report_id_cannot_escape_the_reports_directory(tmp_path: Path) -> None:
    secret = tmp_path / "secret.json"
    secret.write_text('{"schema": "nami.intelligence.report", "escaped": true}\n', encoding="utf-8")
    assert read_report(tmp_path, "../secret.json") is None
    assert read_report(tmp_path, "..") is None
    assert read_report(tmp_path, "/etc/passwd") is None
    document = {"schema": "nami.intelligence.report", "report_id": "rpt-old", "scope": "repository"}
    save_report(tmp_path, document, {"report_id": "rpt-old"})
    assert read_report(tmp_path, "rpt-old")["report_id"] == "rpt-old"


def test_kubernetes_service_name_stays_linear_on_repeated_indented_lines(tmp_path: Path) -> None:
    manifest = tmp_path / "payments.yaml"
    body = "kind: Service\nmetadata:\n" + ("\t\ta\n" * 4000) + "\tname: payments\n"
    manifest.write_text(body, encoding="utf-8")
    rows = service_aliases(tmp_path, "payments-svc")
    assert rows == [{
        "alias": "payments",
        "service": "payments-svc",
        "source": "kubernetes",
        "file": "payments.yaml",
    }]


def _settings(root: Path) -> Settings:
    repos = root / "repos.txt"
    repos.write_text("repo\n", encoding="utf-8")
    output = root / "output"
    output.mkdir()
    return Settings(root=root, repos_file=repos, output_dir=output, github_token="", ghe_token="")
