#!/usr/bin/env python3
"""Build CI summaries from JUnit, coverage XML, and job result files.

Statistics come from the current run. Nothing here is a product measurement.
"""

from __future__ import annotations

import json
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def main() -> int:
    command = sys.argv[1] if len(sys.argv) > 1 else "summarize"
    if command == "semantic":
        return _write_semantic(Path(sys.argv[2]), Path(sys.argv[3]))
    if command == "frontend":
        return _write_frontend(Path(sys.argv[2]))
    if command == "nightly":
        return _nightly(Path(sys.argv[2]) if len(sys.argv) > 2 else Path("output/gate-validation/latest.json"))
    return _summarize(Path(sys.argv[2]) if len(sys.argv) > 2 else Path("output/ci"))


def _write_semantic(junit: Path, destination: Path) -> int:
    counts = _junit(junit) if junit.is_file() else {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    failed = counts["failures"] + counts["errors"]
    payload = {
        "status": "passed" if junit.is_file() and failed == 0 else "failed",
        "tests": counts["tests"],
        "passed": max(0, counts["tests"] - failed - counts["skipped"]),
        "failures": failed,
        "skipped": counts["skipped"],
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload))
    return 0 if payload["status"] == "passed" else 1


def _write_frontend(destination: Path) -> int:
    vitest = _read_json(Path("output/ci/vitest.json"))
    tests = 0
    failed = 0
    if isinstance(vitest, dict):
        tests = int(((vitest.get("numTotalTests") if vitest.get("numTotalTests") is not None else _nested_total(vitest)) or 0))
        failed = int(vitest.get("numFailedTests") or 0)
    dist = Path("web/dist/assets")
    bundles = []
    if dist.is_dir():
        for path in sorted(dist.glob("*.js")):
            bundles.append({"file": path.name, "bytes": path.stat().st_size})
    test_status = os.environ.get("FRONTEND_TEST_RESULT", "unknown")
    build_status = os.environ.get("FRONTEND_BUILD_RESULT", "unknown")
    payload = {
        "tests": tests,
        "failed": failed,
        "test_status": test_status,
        "build": "pass" if build_status == "success" else build_status,
        "bundles": bundles,
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return 0


def _summarize(root: Path) -> int:
    backend = _junit(_first(root, "backend-junit.xml", "backend-test-results/backend-junit.xml"))
    coverage = _coverage(_first(root, "coverage.xml", "backend-test-results/coverage.xml"))
    semantic = _read_json(_first(root, "semantic-summary.json")) or {}
    reports = _read_json(_first(root, "report-smoke.json")) or {}
    frontend = _read_json(_first(root, "frontend-summary.json")) or {}
    commit = os.environ.get("GITHUB_SHA", "local")
    jobs = {
        "backend": os.environ.get("JOB_BACKEND", ""),
        "semantic": os.environ.get("JOB_SEMANTIC", ""),
        "frontend": os.environ.get("JOB_FRONTEND", ""),
        "reports": os.environ.get("JOB_REPORTS", ""),
        "required": os.environ.get("JOB_REQUIRED", ""),
    }
    passed = max(0, backend["tests"] - backend["failures"] - backend["errors"] - backend["skipped"])
    semantic_failed = int(semantic.get("failures") or 0)
    overall = "PASS" if jobs.get("required") == "success" or (
        not jobs.get("required") and all(value in {"", "success"} for value in jobs.values()) and backend["failures"] + backend["errors"] == 0 and semantic_failed == 0 and reports.get("workspace") == "pass"
    ) else "FAIL"
    if any(value not in {"", "success", "skipped"} for value in (jobs["backend"], jobs["semantic"], jobs["frontend"], jobs["reports"])):
        overall = "FAIL"
    summary = {
        "commit": commit,
        "backend": {
            "tests": backend["tests"],
            "passed": passed,
            "failed": backend["failures"] + backend["errors"],
            "skipped": backend["skipped"],
            "line_coverage_percent": coverage["line"],
            "branch_coverage_percent": coverage["branch"],
        },
        "semantic_contract": {
            "tests": semantic.get("tests", 0),
            "passed": semantic.get("passed", 0),
            "failures": semantic_failed,
        },
        "frontend": {
            "tests": frontend.get("tests", 0),
            "build": frontend.get("build", "unknown"),
            "bundles": frontend.get("bundles") or [],
        },
        "reports": {
            "workspace": reports.get("workspace", "unknown"),
            "service_cluster": reports.get("service_cluster", "unknown"),
            "repository": reports.get("repository", "unknown"),
            "change_review": reports.get("change_review", "unknown"),
            "pdf": reports.get("pdf", "unknown"),
            "markdown": reports.get("markdown", "unknown"),
            "json": reports.get("json", "unknown"),
            "evidence_package": reports.get("evidence_package", "unknown"),
        },
        "jobs": jobs,
        "required_checks": overall,
    }
    root.mkdir(parents=True, exist_ok=True)
    (root / "quality-summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    text = _render(summary)
    print(text)
    step = os.environ.get("GITHUB_STEP_SUMMARY")
    if step:
        with open(step, "a", encoding="utf-8") as handle:
            handle.write(text)
    return 0 if overall == "PASS" else 1


def _nightly(path: Path) -> int:
    payload = _read_json(path) or {}
    metrics = payload.get("metrics") or {}
    lines = [
        "# Nami Trace Nightly Validation",
        "",
        "Python compatibility and demos are recorded by their job results.",
        "",
        "## Boundary Gate validation corpus",
        "",
        f"Scenarios: {payload.get('scenario_count', 'not recorded')}",
        f"Decision accuracy: {_num(metrics.get('decision_accuracy'))}",
        f"False block rate: {_num(metrics.get('false_block_rate'))}",
        f"False pass rate: {_num(metrics.get('false_pass_rate'))}",
        f"Invariant pass rate: {_num(metrics.get('invariant_pass_rate'))}",
        f"Replay reproducibility: {_num(metrics.get('replay_reproducibility_rate'))}",
        "",
        "Per-class metrics are in the uploaded corpus artifact.",
        "",
        "> These figures describe the labelled validation fixture corpus. They are not a production-accuracy guarantee.",
        "",
    ]
    per_class = metrics.get("per_class") or {}
    if per_class:
        lines.append("| Decision | Precision | Recall | Support |")
        lines.append("| --- | --- | --- | --- |")
        for label, item in per_class.items():
            lines.append(f"| {label} | {_num(item.get('precision'))} | {_num(item.get('recall'))} | {item.get('support', '')} |")
        lines.append("")
    text = "\n".join(lines)
    print(text)
    step = os.environ.get("GITHUB_STEP_SUMMARY")
    if step:
        with open(step, "a", encoding="utf-8") as handle:
            handle.write(text)
    return 0 if payload else 1


def _render(summary: dict) -> str:
    backend = summary["backend"]
    semantic = summary["semantic_contract"]
    reports = summary["reports"]
    frontend = summary["frontend"]
    bundles = frontend.get("bundles") or []
    bundle_line = "not recorded"
    if bundles:
        largest = max(bundles, key=lambda item: item.get("bytes") or 0)
        bundle_line = f"{largest['file']} {largest['bytes'] / 1024:.0f} kB"
    jobs = summary.get("jobs") or {}

    def mark(value: str) -> str:
        if value in {"pass", "success", "PASS"}:
            return "PASS"
        if value in {"fail", "failure", "failed", "FAIL"}:
            return "FAIL"
        return value or "not recorded"

    return "\n".join([
        "# Nami Trace CI Quality Summary",
        "",
        f"Commit: `{summary['commit']}`",
        "",
        "Coverage measures executed lines. It does not establish semantic correctness.",
        "The validation suite checks deterministic behavior and labelled fixtures. It does not claim production-outcome accuracy.",
        "",
        "## Backend",
        "",
        f"- Tests: {backend['tests']}",
        f"- Passed: {backend['passed']}",
        f"- Failed: {backend['failed']}",
        f"- Skipped: {backend['skipped']}",
        f"- Line coverage: {backend['line_coverage_percent']:.1f}%" if backend["line_coverage_percent"] is not None else "- Line coverage: not recorded",
        f"- Branch coverage: {backend['branch_coverage_percent']:.1f}%" if backend["branch_coverage_percent"] is not None else "- Branch coverage: not recorded",
        "",
        "## Semantic contract",
        "",
        f"- Checks: {semantic['tests']}",
        f"- Passed: {semantic['passed']}",
        f"- Failed: {semantic['failures']}",
        "",
        "## Reports",
        "",
        f"- Workspace report: {mark(reports['workspace'])}",
        f"- Service Cluster: {mark(reports['service_cluster'])}",
        f"- Repository report: {mark(reports['repository'])}",
        f"- Change report: {mark(reports['change_review'])}",
        f"- PDF export: {mark(reports['pdf'])}",
        f"- Markdown export: {mark(reports['markdown'])}",
        f"- JSON export: {mark(reports['json'])}",
        f"- Evidence package: {mark(reports['evidence_package'])}",
        "",
        "## Frontend",
        "",
        f"- Component tests: {frontend.get('tests', 0)} ({mark(str(frontend.get('test_status') or 'unknown'))})",
        f"- Production build: {mark(str(frontend.get('build') or 'unknown'))}",
        f"- Production JS bundle: {bundle_line}",
        "",
        "## Required checks",
        "",
        f"- Backend test suite: {mark(jobs.get('backend') or 'unknown')}",
        f"- Nami semantic contract: {mark(jobs.get('semantic') or 'unknown')}",
        f"- Frontend checks: {mark(jobs.get('frontend') or 'unknown')}",
        f"- Report and export smoke: {mark(jobs.get('reports') or 'unknown')}",
        f"- Overall: {summary['required_checks']}",
        "",
    ])


def _junit(path: Path) -> dict[str, int]:
    empty = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    if not path.is_file():
        return empty
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    if root.tag == "testsuites" and root.get("tests"):
        suites = [root]
    totals = dict(empty)
    for suite in suites:
        for key in totals:
            totals[key] += int(suite.get(key) or 0)
    return totals


def _coverage(path: Path) -> dict[str, float | None]:
    if not path.is_file():
        return {"line": None, "branch": None}
    root = ET.parse(path).getroot()
    line = root.get("line-rate")
    branch = root.get("branch-rate")
    return {
        "line": float(line) * 100 if line is not None else None,
        "branch": float(branch) * 100 if branch is not None else None,
    }


def _first(root: Path, *names: str) -> Path:
    for name in names:
        candidate = root / name
        if candidate.is_file():
            return candidate
    return root / names[0]


def _read_json(path: Path):
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _nested_total(payload: dict) -> int:
    return int(payload.get("numTotalTests") or 0)


def _num(value) -> str:
    if value is None:
        return "not recorded"
    try:
        return f"{float(value):.3f}"
    except (TypeError, ValueError):
        return str(value)


if __name__ == "__main__":
    raise SystemExit(main())
