"""Evidence Gate rejects locations and claims the evidence cannot support."""

from __future__ import annotations

from dep_intel.review_evidence import apply_evidence_gate


def test_gate_keeps_a_deterministic_finding_and_drops_hallucinations(tmp_path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.js").write_text("line\n" * 10, encoding="utf-8")
    change = {"_files": [{"new_path": "src/app.js", "hunks": [{"new_start": 2, "new_count": 1}]}], "changed_files": [{"new_path": "src/app.js", "change_type": "modified"}], "contract_changes": []}
    valid = _finding("policy", "src/app.js", 2, "policy_violation", "A configured rule matched.")
    missing = _finding("policy", "src/missing.js", 1, "policy_violation", "Missing file.")
    outside = _finding("policy", "src/app.js", 500, "policy_violation", "Bad line.")
    escape = _finding("policy", "../secret", 1, "policy_violation", "Escape.")
    causal = _finding("NRM", "src/app.js", 2, "contract_break", "Function X will crash.", origin="temporal_only")
    llm = _finding("LLM", "src/app.js", 2, "regression_risk", "Maybe a bug.", refs=[])
    accepted, rejected = apply_evidence_gate([valid, missing, outside, escape, causal, llm], change, tmp_path)
    assert [item["claim"] for item in accepted] == ["A configured rule matched."]
    assert len(rejected) == 5


def test_stale_graph_downgrades_structural_evidence(tmp_path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.js").write_text("x\n", encoding="utf-8")
    change = {"_files": [{"new_path": "src/app.js", "hunks": []}], "changed_files": [{"new_path": "src/app.js"}], "contract_changes": []}
    finding = _finding("NRM", "src/app.js", 1, "review_target", "Reachable.", strength="verified")
    accepted, _rejected = apply_evidence_gate([finding], change, tmp_path, alignment="stale")
    assert accepted[0]["evidence_strength"] == "moderate"
    assert accepted[0]["evidence"][0]["stale"] is True


def test_duplicate_findings_collapse() -> None:
    one = _finding("policy", "src/app.js", 2, "policy_violation", "Same.")
    two = _finding("policy", "src/app.js", 2, "policy_violation", "Same wording differs.")
    change = {"_files": [{"new_path": "src/app.js", "hunks": []}], "changed_files": [{"new_path": "src/app.js", "change_type": "modified"}], "contract_changes": []}
    accepted, rejected = apply_evidence_gate([one, two], change, None)
    assert len(accepted) == 1
    assert rejected


def test_removed_api_that_is_still_present_is_rejected() -> None:
    finding = _finding("diff", "src/server.js", 1, "contract_break", "Removed POST /api/auth/validate")
    finding["structured"] = {"contract": {"kind": "operation_removed", "method": "POST", "path": "/api/auth/validate"}}
    change = {"_files": [], "changed_files": [{"new_path": "src/server.js"}], "contract_changes": []}
    accepted, rejected = apply_evidence_gate([finding], change, None)
    assert accepted == []
    assert rejected


def _finding(source: str, path: str, line: int, category: str, claim: str, *, strength: str = "verified", origin: str = "", refs: list | None = None) -> dict:
    return {
        "finding_id": claim,
        "category": category,
        "severity": "high",
        "title": claim,
        "claim": claim,
        "file_path": path,
        "line_start": line,
        "source": source,
        "policy_id": "rule" if source == "policy" else None,
        "evidence_strength": strength,
        "evidence": [{"source": "LLM" if source == "LLM" else "policy", "summary": claim, "stale": False}],
        "structured": {"evidence_origin": origin, "evidence_refs": refs or []},
    }
