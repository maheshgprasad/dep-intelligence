"""Deterministic review policies loaded from nami-review.yaml.

Unknown policy types fail configuration validation. They are not skipped.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from dep_intel.review_models import DEFAULT_BUDGETS, diagnostic

POLICY_TYPES = {
    "forbidden_pattern",
    "required_related_test",
    "required_companion_file",
    "contract_compatibility",
    "forbidden_dependency",
    "required_command",
    "path_restriction",
    "documentation_required",
    "semantic_review",
}

DEFAULT_CONFIG: dict[str, Any] = {
    "schema_version": 1,
    "review": {
        "exact_graph_for_prs": True,
        "max_findings": 20,
        "max_inline_findings": 12,
        "include_temporal_only_candidates": True,
    },
    "context": {
        "instruction_files": ["AGENTS.md", "CLAUDE.md", "README.md"],
        "documentation": ["Docs/**/*.md"],
        "exclude": ["**/node_modules/**", "**/.venv/**", "**/dist/**", "**/build/**"],
    },
    "gate": {
        "incomplete_on_required_verification_failure": True,
        "block_on_verified_critical": True,
        "block_on_verified_high_blocker_policy": True,
        "block_on_breaking_contract_with_known_consumers": True,
        "strict_graph": False,
        "minimum_evidence": "weak",
    },
    "verification": {
        "enabled": True,
        "timeout_seconds": 300,
        "allow_network": False,
        "commands": [],
    },
    "policies": [],
    "budgets": {},
}


def load_review_config(repo: Path | None, settings_root: Path | None = None, raw: dict[str, Any] | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if raw is not None:
        return validate_config(raw)
    for candidate in _candidates(repo, settings_root):
        if candidate.is_file():
            try:
                loaded = yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError as exc:
                return {}, [diagnostic("invalid_policy", f"{candidate.name} is not valid YAML: {exc}", severity="error")]
            if not isinstance(loaded, dict):
                return {}, [diagnostic("invalid_policy", f"{candidate.name} must be a mapping.", severity="error")]
            config, errors = validate_config(loaded)
            config["source_path"] = str(candidate)
            return config, errors
    return validate_config(DEFAULT_CONFIG)


def validate_config(raw: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    errors: list[dict[str, Any]] = []
    config = json.loads(json.dumps(DEFAULT_CONFIG))
    for key in ("review", "context", "gate", "verification"):
        if isinstance(raw.get(key), dict):
            config[key].update(raw[key])
    if isinstance(raw.get("budgets"), dict):
        config["budgets"] = {**DEFAULT_BUDGETS, **raw["budgets"]}
    else:
        config["budgets"] = dict(DEFAULT_BUDGETS)
    policies = raw.get("policies") if "policies" in raw else []
    if not isinstance(policies, list):
        errors.append(diagnostic("invalid_policy", "policies must be a list.", severity="error"))
        policies = []
    seen: set[str] = set()
    kept: list[dict[str, Any]] = []
    for item in policies:
        if not isinstance(item, dict) or not item.get("id"):
            errors.append(diagnostic("invalid_policy", "A policy is missing an id.", severity="error"))
            continue
        policy_id = str(item["id"])
        if policy_id in seen:
            errors.append(diagnostic("invalid_policy", f"Duplicate policy id {policy_id}.", severity="error"))
            continue
        seen.add(policy_id)
        check = item.get("check") or {}
        if not isinstance(check, dict) or check.get("type") not in POLICY_TYPES:
            errors.append(diagnostic("invalid_policy", f"Policy {policy_id} has an unknown check type.", severity="error"))
            continue
        severity = str(item.get("severity") or "medium")
        if severity not in {"critical", "high", "medium", "low", "info"}:
            errors.append(diagnostic("invalid_policy", f"Policy {policy_id} has an unknown severity.", severity="error"))
            continue
        kept.append(item)
    config["policies"] = kept
    config["config_hash"] = hashlib.sha256(json.dumps(config, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]
    return config, errors


def evaluate_policies(config: dict[str, Any], change: dict[str, Any], repo_root: Path | None) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for policy in config.get("policies") or []:
        results.extend(_one(policy, change, repo_root))
    return results


def path_match(pattern: str, path: str) -> bool:
    pattern = pattern.replace("\\", "/")
    path = path.replace("\\", "/")
    return _match_parts(PurePosixPath(pattern).parts, PurePosixPath(path).parts)


def _match_parts(pattern: tuple[str, ...], parts: tuple[str, ...]) -> bool:
    if not pattern:
        return not parts
    head, rest = pattern[0], pattern[1:]
    if head == "**":
        return _match_parts(rest, parts) or bool(parts) and _match_parts(pattern, parts[1:])
    if not parts:
        return False
    if fnmatch.fnmatchcase(parts[0], head):
        return _match_parts(rest, parts[1:])
    return False


def _one(policy: dict[str, Any], change: dict[str, Any], repo_root: Path | None) -> list[dict[str, Any]]:
    check = policy["check"]
    kind = check["type"]
    files = _scoped(policy, change)
    base = {
        "policy_id": policy["id"],
        "description": policy.get("description") or "",
        "severity": policy.get("severity") or "medium",
        "blocking": bool(policy.get("blocking")),
        "check": kind,
    }
    if kind == "semantic_review":
        return [{**base, "applied": False, "result": "skipped", "evidence": "Semantic review runs only when a model is configured."}]
    if not files and kind not in {"contract_compatibility", "documentation_required"}:
        return [{**base, "applied": False, "result": "skipped", "evidence": "No changed file matched the policy scope."}]
    if kind == "forbidden_pattern":
        return _patterns(base, files, check)
    if kind == "required_related_test":
        return _tests(base, files, change)
    if kind == "required_companion_file":
        return _companions(base, files, change, check)
    if kind == "contract_compatibility":
        return _contracts(base, change)
    if kind == "forbidden_dependency":
        return _dependencies(base, files, check)
    if kind == "required_command":
        return [{**base, "applied": True, "result": "requested", "command_id": check.get("command") or "", "evidence": "Verification must run the configured command.", "files": [item.get("new_path") or item.get("old_path") for item in files]}]
    if kind == "path_restriction":
        return [{**base, "applied": True, "result": "violation", "evidence": f"{path} is restricted.", "file_path": path, "line_start": _first_line(item)} for item in files if (path := item.get("new_path") or item.get("old_path"))]
    if kind == "documentation_required":
        return _docs(base, change)
    return [{**base, "applied": False, "result": "skipped", "evidence": "The policy type is not evaluated."}]


def _scoped(policy: dict[str, Any], change: dict[str, Any]) -> list[dict[str, Any]]:
    scope = policy.get("scope") or {}
    repositories = set(scope.get("repositories") or [])
    services = set(scope.get("services") or [])
    paths = list(scope.get("paths") or ["**"])
    excluded = list(scope.get("exclude_paths") or [])
    change_types = set(scope.get("change_types") or [])
    classes = set(scope.get("file_classes") or [])
    matched = []
    for item in change.get("_files") or change.get("changed_files") or []:
        path = item.get("new_path") or item.get("old_path") or ""
        if repositories and change.get("repository") not in repositories and item.get("repository") not in repositories:
            continue
        if services and change.get("service_id") not in services and item.get("service_id") not in services:
            continue
        if change_types and item.get("change_type") not in change_types:
            continue
        if classes and not classes.intersection(item.get("classifications") or []):
            continue
        if not any(path_match(pattern, path) for pattern in paths):
            continue
        if any(path_match(pattern, path) for pattern in excluded):
            continue
        matched.append(item)
    return matched


def _patterns(base: dict[str, Any], files: list[dict[str, Any]], check: dict[str, Any]) -> list[dict[str, Any]]:
    patterns = [str(item) for item in check.get("patterns") or []]
    hits = []
    for item in files:
        for hunk in item.get("hunks") or []:
            for offset, line in enumerate(hunk.get("added_lines") or []):
                if any(pattern and pattern in line for pattern in patterns):
                    line_no = (hunk.get("changed_new_lines") or [None])[offset] if offset < len(hunk.get("changed_new_lines") or []) else hunk.get("new_start")
                    hits.append({
                        **base,
                        "applied": True,
                        "result": "violation",
                        "file_path": item.get("new_path") or item.get("old_path"),
                        "line_start": line_no,
                        "evidence": "An added line contains a configured forbidden pattern.",
                        "added_line": line,
                    })
    if hits:
        return hits
    return [{**base, "applied": True, "result": "passed", "evidence": "No added line contained a forbidden pattern."}]


def _tests(base: dict[str, Any], files: list[dict[str, Any]], change: dict[str, Any]) -> list[dict[str, Any]]:
    changed_tests = [item for item in (change.get("_files") or change.get("changed_files") or []) if "test" in (item.get("classifications") or [])]
    results = []
    for item in files:
        if "source" not in (item.get("classifications") or []) or "test" in (item.get("classifications") or []):
            continue
        path = item.get("new_path") or item.get("old_path") or ""
        stem = PurePosixPath(path).stem
        related = any(stem and stem in (test.get("new_path") or test.get("old_path") or "") for test in changed_tests)
        tested = any(symbol.get("tested_by") for symbol in change.get("changed_symbols") or [] if symbol.get("file_path") == path)
        if related or tested:
            results.append({**base, "applied": True, "result": "passed", "file_path": path, "evidence": "A changed test or a TESTED_BY link covers this file."})
        else:
            results.append({**base, "applied": True, "result": "violation", "file_path": path, "line_start": _first_line(item), "evidence": "Production code changed and no related test change or TESTED_BY link was found."})
    if not results:
        return [{**base, "applied": False, "result": "skipped", "evidence": "No production source in scope changed."}]
    return results


def _companions(base: dict[str, Any], files: list[dict[str, Any]], change: dict[str, Any], check: dict[str, Any]) -> list[dict[str, Any]]:
    companion = str(check.get("companion") or "")
    changed_paths = [item.get("new_path") or item.get("old_path") or "" for item in (change.get("_files") or change.get("changed_files") or [])]
    present = any(path_match(companion, path) for path in changed_paths) if companion else False
    if present:
        return [{**base, "applied": True, "result": "passed", "evidence": "The companion path also changed."}]
    return [{**base, "applied": True, "result": "violation", "file_path": files[0].get("new_path") or files[0].get("old_path"), "evidence": f"Expected a change matching {companion}."}]


def _contracts(base: dict[str, Any], change: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for item in change.get("contract_changes") or []:
        if not item.get("breaking"):
            rows.append({**base, "applied": True, "result": "passed", "evidence": item.get("message") or item.get("kind"), "contract": item})
            continue
        consumers = item.get("consumers") or []
        rows.append({
            **base,
            "applied": True,
            "result": "violation" if consumers else "warning",
            "evidence": item.get("message") or item.get("kind"),
            "file_path": item.get("file_path"),
            "line_start": item.get("line"),
            "contract": item,
            "consumers": consumers,
        })
    if not rows:
        return [{**base, "applied": False, "result": "skipped", "evidence": "No contract change was detected."}]
    return rows


def _dependencies(base: dict[str, Any], files: list[dict[str, Any]], check: dict[str, Any]) -> list[dict[str, Any]]:
    forbidden = {str(item) for item in check.get("packages") or []}
    hits = []
    for item in files:
        if "dependency_manifest" not in (item.get("classifications") or []):
            continue
        for hunk in item.get("hunks") or []:
            for line in hunk.get("added_lines") or []:
                for package in forbidden:
                    if package and package in line:
                        hits.append({**base, "applied": True, "result": "violation", "file_path": item.get("new_path"), "evidence": f"Added dependency line mentions {package}.", "added_line": line})
    if hits:
        return hits
    return [{**base, "applied": True, "result": "passed", "evidence": "No forbidden dependency was introduced."}]


def _docs(base: dict[str, Any], change: dict[str, Any]) -> list[dict[str, Any]]:
    files = change.get("_files") or change.get("changed_files") or []
    source = [item for item in files if "source" in (item.get("classifications") or [])]
    docs = [item for item in files if "documentation" in (item.get("classifications") or [])]
    if not source:
        return [{**base, "applied": False, "result": "skipped", "evidence": "No source file changed."}]
    if docs:
        return [{**base, "applied": True, "result": "passed", "evidence": "Documentation changed with the source."}]
    return [{**base, "applied": True, "result": "violation", "file_path": source[0].get("new_path"), "evidence": "Source changed without a documentation change."}]


def _first_line(item: dict[str, Any]) -> int | None:
    hunks = item.get("hunks") or []
    if not hunks:
        return None
    lines = hunks[0].get("changed_new_lines") or hunks[0].get("changed_old_lines") or []
    return lines[0] if lines else hunks[0].get("new_start")


def _candidates(repo: Path | None, settings_root: Path | None) -> list[Path]:
    found = []
    if repo is not None:
        found.append(repo / "nami-review.yaml")
    if settings_root is not None and settings_root != repo:
        found.append(settings_root / "nami-review.yaml")
    return found
