from pathlib import Path

from dep_intel.config import Settings
from dep_intel.languages import detect
from dep_intel.manifests import build_matrix, packages_in
from dep_intel.api_deps import detect_api_dependencies
from dep_intel.review import review
from dep_intel.sources import load_repos, load_workspace
from dep_intel.updates import classify
import httpx


ROOT = Path(__file__).resolve().parents[2]


def test_fixtures_find_mismatch_and_api_edge(tmp_path: Path) -> None:
    settings = Settings(
        root=ROOT,
        repos_file=ROOT / "repos.txt",
        output_dir=tmp_path,
        github_token="",
        ghe_token="",
    )
    with httpx.Client() as client:
        workspaces = [load_workspace(repo, client, settings) for repo in load_repos(settings)]
    assert {workspace.ref.name for workspace in workspaces} == {
        "auth-service",
        "user-service",
        "report-job",
    }
    detect(workspaces, settings)
    matrix = build_matrix(workspaces, settings)
    assert matrix["summary"]["total_repos"] == 3
    written = (tmp_path / "dep_matrix.json").read_text(encoding="utf-8")
    assert "express" in written
    assert "version_mismatches" in written

    apis = detect_api_dependencies(workspaces, settings)
    assert apis["summary"]["total_dependencies"] == 1

    findings = review(workspaces, settings)
    assert findings["summary"]["dead_code"] >= 2


def test_package_parse_and_semver() -> None:
    packages = packages_in(
        {"package.json": '{"dependencies": {"express": "^4.18.2"}}', "requirements.txt": "flask==2.3.3\n"}
    )
    assert {(package.ecosystem, package.name) for package in packages} == {("npm", "express"), ("pypi", "flask")}
    assert classify("4.17.1", "4.18.2") == "minor"
    assert classify("1.6.0", "2.0.0") == "major"
    assert classify("1.6.0", "1.6.0") is None
