"""Change View parsing, classification, and symbol mapping."""

from __future__ import annotations

import subprocess
from pathlib import Path

from dep_intel.change_view import (
    build_change_view,
    classify_path,
    graph_alignment,
    map_changed_symbols,
    parse_name_status,
    parse_numstat,
    parse_unified_diff,
    safe_relative,
)
from dep_intel.config import Settings
from dep_intel.graph_models import CODE_SYMBOL, FILE_ANCHOR, GraphNode, Partition, Snapshot, symbol_key
from dep_intel.review_sources import resolve_change, safe_ref


def test_classify_manifest_without_a_model() -> None:
    assert "dependency_manifest" in classify_path("package.json")
    assert "dependency_lockfile" in classify_path("package-lock.json")
    assert "test" in classify_path("tests/test_auth.py")
    assert "source" in classify_path("src/server.js")


def test_unified_diff_covers_add_delete_rename_binary_and_spaces() -> None:
    text = """diff --git a/src/old.js b/src/new.js
similarity index 90%
rename from src/old.js
rename to src/new.js
index 1111111..2222222 100644
--- a/src/old.js
+++ b/src/new.js
@@ -1,0 +2,1 @@
+kept
diff --git a/src/added.js b/src/added.js
new file mode 100644
index 0000000..1111111
--- /dev/null
+++ b/src/added.js
@@ -0,0 +1,1 @@
+created
diff --git a/src/gone.js b/src/gone.js
deleted file mode 100644
index 1111111..0000000
--- a/src/gone.js
+++ /dev/null
@@ -1,1 +0,0 @@
-removed
diff --git "a/my file.js" "b/my file.js"
index 1111111..2222222 100644
--- "a/my file.js"
+++ "b/my file.js"
@@ -3 +3 @@
-old
+new
diff --git a/assets/logo.png b/assets/logo.png
new file mode 100644
index 0000000..1111111
Binary files /dev/null and b/assets/logo.png differ
diff --git a/../secret b/../secret
--- a/../secret
+++ b/../secret
@@ -1 +1 @@
-a
+b
"""
    files, diagnostics = parse_unified_diff(text)
    by_new = {item["new_path"]: item for item in files}
    assert by_new["src/new.js"]["change_type"] == "renamed"
    assert by_new["src/added.js"]["change_type"] == "added"
    assert by_new["src/added.js"]["hunks"][0]["changed_new_lines"] == [1]
    gone = next(item for item in files if item["old_path"] == "src/gone.js")
    assert gone["change_type"] == "deleted"
    assert gone["hunks"][0]["changed_old_lines"] == [1]
    assert by_new["my file.js"]["hunks"][0]["added_lines"] == ["new"]
    assert by_new["assets/logo.png"]["binary"] is True
    assert all(item["new_path"] != "../secret" and item["old_path"] != "../secret" for item in files)
    assert diagnostics


def test_malformed_hunk_is_a_diagnostic() -> None:
    _files, diagnostics = parse_unified_diff("@@ -1 +1 @@\n+no header\n")
    assert any(item["code"] == "malformed_diff" for item in diagnostics)


def test_name_status_and_numstat_null_format() -> None:
    status = parse_name_status("R100\0src/old.js\0src/new.js\0M\0src/app.js\0")
    assert status[0]["change_type"] == "renamed"
    assert status[1]["new_path"] == "src/app.js"
    stats = parse_numstat("1\t2\0src/old.js\0src/new.js\0-\t-\tsrc/logo.png\0")
    assert stats[0]["old_path"] == "src/old.js"
    assert stats[1]["binary"] is True


def test_path_traversal_and_unsafe_ref() -> None:
    assert safe_relative("../etc/passwd") is None
    assert safe_relative("/tmp/passwd") is None
    assert safe_relative("src/app.js") == "src/app.js"
    assert safe_ref("--upload-pack=evil") is False
    assert safe_ref("feature/auth") is True


def test_line_to_symbol_picks_the_narrowest_and_reports_ambiguity() -> None:
    outer = _node("auth", "outer", 1, 20)
    inner = _node("auth", "inner", 4, 6)
    other = _node("auth", "other", 4, 6)
    snapshot = _snapshot([outer, inner, other])
    changed = {"new_path": "src/app.js", "old_path": "src/app.js", "change_type": "modified", "service_id": "auth", "hunks": [{"changed_new_lines": [5], "changed_old_lines": [5]}]}
    symbols, diagnostics = map_changed_symbols(changed, snapshot, None, "g1")
    assert len(symbols) == 1
    assert symbols[0]["name"] in {"inner", "other"}
    assert any(item["code"] == "ambiguous_symbol" for item in diagnostics)


def test_deleted_symbol_uses_the_base_graph() -> None:
    removed = _node("auth", "validate", 8, 12)
    base = _snapshot([removed])
    empty = _snapshot([])
    changed = {"new_path": None, "old_path": "src/app.js", "change_type": "deleted", "service_id": "auth", "hunks": [{"changed_old_lines": [9], "changed_new_lines": []}]}
    symbols, _diagnostics = map_changed_symbols(changed, empty, base, "base")
    assert symbols[0]["qualified_name"].endswith("validate")
    assert symbols[0]["topology"] == "previous"


def test_alignment_does_not_call_a_different_sha_exact() -> None:
    snapshot = _snapshot([_node("auth", "validate", 1, 4)], head="abc")
    assert graph_alignment(snapshot, "auth", "abc", "abc", dirty=False) == "exact"
    assert graph_alignment(snapshot, "auth", "abc", "abc", dirty=True) == "compatible"
    assert graph_alignment(snapshot, "auth", "abc", "def", dirty=False) == "stale"
    assert graph_alignment(None, "auth", "abc", "def", dirty=False) == "unavailable"


def test_git_range_resolves_shas_and_rejects_missing_refs(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    settings = _settings(tmp_path, repo)
    (repo / "src").mkdir()
    (repo / "src" / "app.js").write_text("function a() {}\n", encoding="utf-8")
    _git(repo, "add", "src/app.js")
    _git(repo, "commit", "-m", "base")
    base = _git_out(repo, "rev-parse", "HEAD")
    (repo / "src" / "app.js").write_text("function a() { return 1 }\n", encoding="utf-8")
    _git(repo, "commit", "-am", "head")
    head = _git_out(repo, "rev-parse", "HEAD")
    resolved = resolve_change(repo, {"kind": "range", "base_ref": base, "head_ref": head}, settings)
    assert resolved["base_sha"] == base
    assert resolved["head_sha"] == head
    view = build_change_view(resolved, service_id="auth", repository=str(repo), snapshot=None, base_snapshot=None, repo_root=repo)
    assert view["changed_files"][0]["new_path"] == "src/app.js"
    missing = resolve_change(repo, {"kind": "range", "base_ref": "does-not-exist", "head_ref": head}, settings)
    assert any(item["code"] == "git_ref_not_found" for item in missing["diagnostics"])
    assert missing["head_sha"] in {None, head}


def _node(service: str, name: str, start: int, end: int, file: str = "src/app.js") -> GraphNode:
    qualified = f"{file}::{name}"
    return GraphNode(
        key=symbol_key(service, file, qualified, "Function"),
        category=CODE_SYMBOL,
        service_id=service,
        name=name,
        qualified_name=qualified,
        file_path=file,
        line_start=start,
        line_end=end,
        kind="Function",
    )


def _snapshot(nodes: list[GraphNode], head: str = "abc") -> Snapshot:
    part = Partition(service_id="auth", repository="auth", workspace_head=head)
    for node in nodes:
        part.nodes[node.key] = node
    anchor = GraphNode(key=symbol_key("auth", "src/app.js", "src/app.js", "File"), category=FILE_ANCHOR, service_id="auth", name="app.js", qualified_name="src/app.js", file_path="src/app.js", kind="File")
    part.nodes[anchor.key] = anchor
    return Snapshot(
        version="g1",
        generated_at="",
        partitions={"auth": part},
        nodes={**part.nodes},
        edges=[],
        forward={},
        reverse={},
        by_service={},
        by_file={},
        by_name={},
        contracts={},
        revision_vector={},
    )


def _settings(root: Path, repo: Path) -> Settings:
    repos = root / "repos.txt"
    repos.write_text(f"{repo.name}\n", encoding="utf-8")
    output = root / "output"
    output.mkdir()
    return Settings(root=root, repos_file=repos, output_dir=output, github_token="", ghe_token="")


def _git_repo(root: Path) -> Path:
    repo = root / "repo"
    repo.mkdir()
    _git(repo, "init")
    return repo


def _git(repo: Path, *args: str, env: dict | None = None) -> None:
    merged = {"GIT_AUTHOR_NAME": "Nami", "GIT_AUTHOR_EMAIL": "nami@example.com", "GIT_COMMITTER_NAME": "Nami", "GIT_COMMITTER_EMAIL": "nami@example.com"}
    if env:
        merged.update(env)
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env={**dict(**__import__("os").environ), **merged})


def _git_out(repo: Path, *args: str) -> str:
    completed = subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)
    return completed.stdout.strip()
