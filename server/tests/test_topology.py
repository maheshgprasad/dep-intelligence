"""Topology discovery does not treat a shared route name as a provider."""

from __future__ import annotations

from pathlib import Path

from dep_intel.config import Settings
from dep_intel.sources import load_repos
from dep_intel.topology.discovery import discover
from dep_intel.topology.resolver import resolve


ROOT = Path(__file__).resolve().parents[2]


def test_relative_route_without_a_host_stays_unresolved() -> None:
    result = resolve(
        [{"service": "auth-service"}, {"service": "user-service"}],
        [{"service": "user-service", "method": "POST", "url": "/api/auth/validate", "file": "src/users.js"}],
        [],
        [],
    )
    assert result["relationships"] == []
    assert result["unresolved"][0]["path"] == "/api/auth/validate"


def test_host_match_records_provenance() -> None:
    result = resolve(
        [{"service": "auth-service"}],
        [{"service": "user-service", "method": "POST", "url": "http://auth-service:3001/api/auth/validate", "file": "src/users.js"}],
        [{"alias": "auth-service", "service": "auth-service"}],
        [],
    )
    assert result["relationships"][0]["to"] == "auth-service"
    assert result["relationships"][0]["reasons"]


def test_fixture_manifest_binding_is_compatibility_not_route_guessing(tmp_path: Path) -> None:
    repos = tmp_path / "repos.txt"
    repos.write_text("fixtures/auth-service\nfixtures/user-service\nfixtures/report-job\n", encoding="utf-8")
    settings = Settings(root=ROOT, repos_file=repos, output_dir=tmp_path, github_token="", ghe_token="")
    loaded = load_repos(settings)
    assert {item.name for item in loaded} == {"auth-service", "user-service", "report-job"}
    payload = discover(settings)
    linked = {(item["from"], item["to"]) for item in payload["relationships"]}
    assert ("user-service", "auth-service") in linked
    assert all("route name" in item["message"] or item["host"] or item["path"] for item in payload["unresolved"])
