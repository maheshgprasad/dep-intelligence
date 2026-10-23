"""Deterministic policy matching."""

from __future__ import annotations

from dep_intel.review_policy import evaluate_policies, load_review_config, path_match, validate_config


def _change(files: list[dict], contracts: list | None = None) -> dict:
    return {"repository": "auth", "service_id": "auth", "_files": files, "changed_files": files, "contract_changes": contracts or [], "changed_symbols": []}


def test_scope_match_exclude_and_duplicate_ids() -> None:
    assert path_match("src/auth/**", "src/auth/token.js")
    assert not path_match("src/auth/**", "src/users.js")
    config, errors = validate_config({
        "policies": [
            {"id": "one", "severity": "high", "check": {"type": "forbidden_pattern", "patterns": ["X"]}},
            {"id": "one", "severity": "high", "check": {"type": "forbidden_pattern", "patterns": ["Y"]}},
            {"id": "typo", "severity": "high", "check": {"type": "not_a_policy"}},
        ]
    })
    assert any("Duplicate" in item["message"] for item in errors)
    assert any("unknown" in item["message"] for item in errors)
    assert [item["id"] for item in config["policies"]] == ["one"]


def test_each_deterministic_policy_matches_and_skips() -> None:
    secret = _change([{"new_path": "src/a.js", "classifications": ["source"], "change_type": "modified", "hunks": [{"added_lines": ["PLACEHOLDER_SECRET_VALUE"], "changed_new_lines": [4], "new_start": 4}]}])
    clean = _change([{"new_path": "src/a.js", "classifications": ["source"], "change_type": "modified", "hunks": [{"added_lines": ["ok"], "changed_new_lines": [4]}]}])
    results = evaluate_policies({"policies": [_policy("secret", "forbidden_pattern", patterns=["PLACEHOLDER_SECRET_VALUE"])]}, secret, None)
    assert results[0]["result"] == "violation"
    assert results[0]["severity"] == "critical"
    assert evaluate_policies({"policies": [_policy("secret", "forbidden_pattern", patterns=["PLACEHOLDER_SECRET_VALUE"])]}, clean, None)[0]["result"] == "passed"
    skipped = evaluate_policies({"policies": [_policy("secret", "forbidden_pattern", patterns=["X"], paths=["docs/**"])]}, secret, None)
    assert skipped[0]["result"] == "skipped"
    gap = evaluate_policies({"policies": [_policy("tests", "required_related_test", severity="high")]}, secret, None)
    assert gap[0]["result"] == "violation"
    companion = evaluate_policies({"policies": [{"id": "mig", "severity": "medium", "check": {"type": "required_companion_file", "companion": "migrations/**"}}]}, secret, None)
    assert companion[0]["result"] == "violation"
    contracts = evaluate_policies({"policies": [_policy("api", "contract_compatibility")]}, _change([], [{"kind": "operation_removed", "breaking": True, "message": "Removed POST /api/auth/validate", "file_path": "src/server.js", "method": "POST", "path": "/api/auth/validate", "consumers": [{"service_id": "user-service"}]}]), None)
    assert contracts[0]["result"] == "violation"
    dependency = evaluate_policies({"policies": [{"id": "dep", "severity": "high", "scope": {"file_classes": ["dependency_manifest"]}, "check": {"type": "forbidden_dependency", "packages": ["left-pad"]}}]}, _change([{"new_path": "package.json", "classifications": ["dependency_manifest"], "hunks": [{"added_lines": ['"left-pad": "1.0.0"'], "changed_new_lines": [2]}]}]), None)
    assert dependency[0]["result"] == "violation"
    command = evaluate_policies({"policies": [_policy("cmd", "required_command")]}, secret, None)
    assert command[0]["result"] == "requested"
    restricted = evaluate_policies({"policies": [_policy("path", "path_restriction", paths=["src/a.js"])]}, secret, None)
    assert restricted[0]["result"] == "violation"
    docs = evaluate_policies({"policies": [_policy("docs", "documentation_required")]}, secret, None)
    assert docs[0]["result"] == "violation"


def test_malformed_yaml(tmp_path) -> None:
    (tmp_path / "nami-review.yaml").write_text("policies: [", encoding="utf-8")
    _config, errors = load_review_config(tmp_path)
    assert errors[0]["code"] == "invalid_policy"


def _policy(policy_id: str, kind: str, *, patterns: list[str] | None = None, paths: list[str] | None = None, severity: str = "critical") -> dict:
    check: dict = {"type": kind}
    if patterns is not None:
        check["patterns"] = patterns
    if kind == "required_command":
        check["command"] = "unit"
    return {"id": policy_id, "description": policy_id, "severity": severity, "blocking": True, "scope": {"paths": paths or ["**"]}, "check": check}
