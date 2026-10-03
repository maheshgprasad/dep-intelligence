import json
import subprocess
from pathlib import Path

from dep_intel.config import Settings
from dep_intel.coverage import _one
from dep_intel.languages import detect
from dep_intel.manifests import build_matrix, packages_in
from dep_intel.api_deps import detect_api_dependencies
from dep_intel.review import review
from dep_intel.sources import RepoRef, analysis_sources, load_repos, load_workspace
from dep_intel.updates import classify
import httpx


ROOT = Path(__file__).resolve().parents[2]


def test_fixtures_find_mismatch_and_api_edge(tmp_path: Path) -> None:
    repos = tmp_path / "repos.txt"
    repos.write_text("fixtures/auth-service\nfixtures/user-service\nfixtures/report-job\n", encoding="utf-8")
    settings = Settings(
        root=ROOT,
        repos_file=repos,
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

    written = json.loads((tmp_path / "api_dependencies.json").read_text(encoding="utf-8"))
    routes = written["api_dependencies"]["services"]["user-service"]["exposes"]
    assert {item["path"] for item in routes} == {"/api/users"}

    findings = review(workspaces, settings)
    assert findings["summary"]["dead_code"] >= 2


def test_shallow_checkout_is_removed_after_reports(tmp_path: Path, monkeypatch) -> None:
    origin = tmp_path / "origin"
    origin.mkdir()
    (origin / "src").mkdir()
    (origin / "src" / "server.js").write_text(
        "const express = require('express');\nconst app = express();\napp.get('/health', function health(_req, res) { res.send('ok'); });\n",
        encoding="utf-8",
    )
    (origin / "app.py").write_text("def answer():\n    return 1\n", encoding="utf-8")
    (origin / "test_app.py").write_text("from app import answer\n\ndef test_answer():\n    assert answer() == 1\n", encoding="utf-8")
    _git(origin, "init")
    _git(origin, "add", ".")
    _git(origin, "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "-m", "init")
    ref = RepoRef(raw=str(origin), name="o/demo", slug="o_demo", kind="github", host="github.com", owner="o", repo="demo")
    monkeypatch.setattr("dep_intel.sources.load_repos", lambda _settings: [ref])
    settings = Settings(root=ROOT, repos_file=tmp_path / "repos.txt", output_dir=tmp_path / "out", github_token="", ghe_token="")
    with analysis_sources(settings) as workspaces:
        checkout = settings.output_dir / "work" / "o_demo"
        assert checkout.is_dir()
        assert "src/server.js" in workspaces[0].files
        detect_api_dependencies(workspaces, settings)
        coverage = _one(workspaces[0], "python")
    assert not checkout.exists()
    assert origin.is_dir()
    written = json.loads((settings.output_dir / "api_dependencies.json").read_text(encoding="utf-8"))
    routes = written["api_dependencies"]["services"]["o/demo"]["exposes"]
    assert {item["path"] for item in routes} == {"/health"}
    assert "Clone the repository" not in coverage["message"]
    assert "shallow checkout is deleted" in coverage["message"]


def test_local_allowlist_path_is_not_deleted(tmp_path: Path) -> None:
    repos = tmp_path / "repos.txt"
    repos.write_text("fixtures/auth-service\n", encoding="utf-8")
    settings = Settings(root=ROOT, repos_file=repos, output_dir=tmp_path / "out", github_token="", ghe_token="")
    with analysis_sources(settings) as workspaces:
        assert workspaces[0].content_source == "local"
        assert "src/server.js" in workspaces[0].files
    assert (ROOT / "fixtures" / "auth-service").is_dir()


def _git(root: Path, *args: str) -> None:
    completed = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stderr


def test_python_http_routes_are_listed(tmp_path: Path) -> None:
    repo = tmp_path / "service"
    repo.mkdir()
    (repo / "main.py").write_text(
        '@app.get("/health")\n@app.post("/chat")\n@app.get("/bot/{query}")\n',
        encoding="utf-8",
    )
    (repo / "tests").mkdir()
    (repo / "tests" / "test_main.py").write_text('@app.get("/not-a-route")\n', encoding="utf-8")
    repos = tmp_path / "repos.txt"
    repos.write_text(f"{repo}\n", encoding="utf-8")
    settings = Settings(root=ROOT, repos_file=repos, output_dir=tmp_path / "out", github_token="", ghe_token="")
    with httpx.Client() as client:
        workspaces = [load_workspace(item, client, settings) for item in load_repos(settings)]
    detect_api_dependencies(workspaces, settings)
    written = json.loads((settings.output_dir / "api_dependencies.json").read_text(encoding="utf-8"))
    routes = {(item["method"], item["path"]) for item in written["api_dependencies"]["services"]["service"]["exposes"]}
    assert routes == {("GET", "/health"), ("POST", "/chat"), ("GET", "/bot/{query}")}


def test_package_parse_and_semver() -> None:
    packages = packages_in(
        {"package.json": '{"dependencies": {"express": "^4.18.2"}}', "requirements.txt": "flask==2.3.3\n"}
    )
    assert {(package.ecosystem, package.name) for package in packages} == {("npm", "express"), ("pypi", "flask")}
    assert classify("4.17.1", "4.18.2") == "minor"
    assert classify("1.6.0", "2.0.0") == "major"
    assert classify("1.6.0", "1.6.0") is None
