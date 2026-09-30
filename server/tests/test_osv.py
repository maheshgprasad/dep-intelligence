import json
from pathlib import Path

import httpx

from dep_intel.config import Settings
from dep_intel.manifests import packages_in
from dep_intel.sources import RepoRef, Workspace
from dep_intel.vulns import scan_vulnerabilities


def test_each_package_is_queried_and_a_fixed_version_is_suggested(tmp_path: Path) -> None:
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.host == "pypi.org":
            return httpx.Response(200, json={"info": {"version": "2.32.3"}})
        body = json.loads(request.content)
        calls.append(body)
        name = body["package"]["name"]
        if name == "flask":
            return httpx.Response(
                200,
                json={
                    "vulns": [
                        {
                            "id": "PYSEC-1",
                            "summary": "Example advisory",
                            "affected": [
                                {
                                    "package": {"name": "flask", "ecosystem": "PyPI"},
                                    "ranges": [{"events": [{"introduced": "0"}, {"fixed": "2.2.5"}, {"fixed": "2.3.4"}]}],
                                }
                            ],
                        }
                    ]
                },
            )
        return httpx.Response(200, json={"vulns": []})

    workspace = Workspace(
        ref=RepoRef(raw="fixtures/report-job", name="report-job", slug="report-job", kind="local"),
        files={
            "requirements.txt": "flask==2.3.3\nrequests\n",
            "package-lock.json": json.dumps(
                {"packages": {"": {}, "node_modules/express": {"version": "4.17.1"}}}
            ),
        },
    )
    settings = Settings(root=tmp_path, repos_file=tmp_path / "repos.txt", output_dir=tmp_path, github_token="", ghe_token="")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = scan_vulnerabilities([workspace], settings, client)

    queried = {(item["package"]["ecosystem"], item["package"]["name"], item["version"]) for item in calls}
    assert queried == {("PyPI", "flask", "2.3.3"), ("PyPI", "requests", "2.32.3"), ("npm", "express", "4.17.1")}
    written = json.loads((tmp_path / "vulnerabilities.json").read_text(encoding="utf-8"))
    assert written["meta"]["scanned"] == 3
    flask = next(item for item in written["suggestions"] if item["package"] == "flask")
    assert flask["fixed"] == "2.3.4"
    assert "Upgrade flask from 2.3.3 to 2.3.4" in flask["suggestion"]
    pinned = next(item for item in written["suggestions"] if item["package"] == "requests")
    assert "Pin requests to 2.32.3" in pinned["suggestion"]
    assert "Queried 3 packages" in result["message"]


def test_lockfile_version_replaces_the_declared_range() -> None:
    packages = packages_in(
        {
            "package.json": '{"dependencies": {"express": "^4.17.1"}}',
            "package-lock.json": json.dumps({"packages": {"node_modules/express": {"version": "4.18.2"}}}),
        }
    )
    assert {(package.ecosystem, package.name, package.version) for package in packages} == {("npm", "express", "4.18.2")}
