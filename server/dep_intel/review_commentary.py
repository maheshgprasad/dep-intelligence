"""Turn a validated Gate result into short operational language.

The model explains stored facts. It does not decide, grade evidence, or add identities.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from dep_intel.review_model_provider import provider_from_env
from dep_intel.sources import redact
from dep_intel.store import now

log = logging.getLogger("nami.boundary")

COMMENTARY_VERSION = "2"
COMMENTARY_TIMEOUT = 20.0
COMMENTARY_PURPOSE = "commentary"

_LIMITS = {
    "headline": {"chars": 80, "sentences": 1, "words": 12},
    "summary": {"chars": 320, "sentences": 2, "words": 60},
    "why_it_matters": {"chars": 280, "sentences": 2, "words": 50},
    "next_action": {"chars": 200, "sentences": 1, "words": 40},
    "evidence_comment": {"chars": 220, "sentences": 1, "words": 40},
    "validation_comment": {"chars": 240, "sentences": 1, "words": 40},
    "limitation_note": {"chars": 220, "sentences": 1, "words": 40},
}
_FIELDS = tuple(_LIMITS)
_FORBIDDEN_KEYS = {"decision", "evidence_grade", "evidence_strength", "severity", "blocking_findings", "gate", "policy_outcome"}
_IDENTITY = (
    re.compile(r"\bBG-[A-Z]+-\d+\b"),
    re.compile(r"\bEV-\d+\b"),
    re.compile(r"\bINF-\d+\b"),
    re.compile(r"\bACT-\d+\b"),
    re.compile(r"\b[A-Za-z0-9]+-service\b"),
    re.compile(r"\b[\w./-]+\.(?:js|jsx|ts|tsx|py|go|java|yml|yaml|json|md)\b"),
)
_FORBIDDEN_LANGUAGE = re.compile(
    r"(\d+\s*%)|(\bprobability\b)|(\bwill definitely\b)|(\bguaranteed outage\b)|(\bwill crash production\b)"
    r"|(\bcertainly safe\b)|(\bzero risk\b)|(\bcatastrophic\b)|(\bwidespread system\b)|(\bsafe to deploy\b)|(\bsafe to proceed\b)",
    re.IGNORECASE,
)
_OVERCLAIM = ("fully validated", "all checks passed", "all 10 configured safety invariants passed")

COMMENTARY_INSTRUCTIONS = (
    "You are a technical summarization component. You do not make the Gate decision. "
    "Use only supplied facts. Do not introduce new services, files, evidence, rules, tests, or probabilities. "
    "Return JSON with keys headline, summary, why_it_matters, next_action, evidence_comment, validation_comment, limitation_note. "
    "Explain what happened, why it matters, what should happen next, what evidence supports the conclusion, "
    "and whether Gate validation succeeded. Keep each field to one or two short sentences. "
    "Do not treat ranking scores as probabilities."
)


def attach_commentary(report: dict[str, Any], provider: Any | None = None) -> dict[str, Any]:
    """Store commentary beside the decision. A model failure does not change the review."""
    if report.get("commentary"):
        return report
    before = (report.get("gate") or {}).get("decision")
    try:
        commentary = build_commentary(report, provider)
    except Exception:
        log.exception("commentary.failed")
        commentary = _pack(_generic(report), report, source="deterministic", mode="deterministic_fallback", provider_name="none", model="")
    body = dict(report)
    body["commentary"] = commentary
    if (body.get("gate") or {}).get("decision") != before:
        gate = dict(body.get("gate") or {})
        gate["decision"] = before
        body["gate"] = gate
    return body


def build_commentary(report: dict[str, Any], provider: Any | None = None) -> dict[str, Any]:
    payload = commentary_input(report)
    validation = report.get("gate_validation") or {}
    if validation.get("status") == "failed":
        text = _failure_copy()
        return _pack(text, report, source="deterministic", mode="deterministic_fallback", provider_name="none", model="", diagnostic="validation_failed")
    rule_ids = [str(item.get("rule_id") or "") for item in payload.get("triggered_rules") or []]
    if payload.get("decision") == "warn" and rule_ids == ["BG-TEMPORAL-001"]:
        text = fallback_commentary(payload)
        return _pack(text, report, source="deterministic", mode="deterministic_fallback", provider_name="none", model="", diagnostic="")
    chosen = provider if provider is not None else provider_from_env()
    model_name = str(getattr(chosen, "model", "") or "")
    provider_name = str(getattr(chosen, "name", "none") or "none")
    complete = getattr(chosen, "complete", None)
    if not callable(complete) or provider_name == "none":
        text = fallback_commentary(payload)
        return _pack(text, report, source="deterministic", mode="deterministic_fallback", provider_name=provider_name, model=model_name)
    try:
        result = complete(COMMENTARY_PURPOSE, payload, timeout=COMMENTARY_TIMEOUT)
    except Exception:
        log.info("commentary.model_failed")
        result = {"available": False, "diagnostic": "model_unavailable"}
    parsed = result.get("parsed") if isinstance(result, dict) else None
    if not isinstance(result, dict) or not result.get("available") or not isinstance(parsed, dict):
        text = fallback_commentary(payload)
        return _pack(text, report, source="deterministic", mode="deterministic_fallback", provider_name=provider_name, model=model_name, diagnostic=str((result or {}).get("diagnostic") or "model_unavailable"))
    ok, reason = validate_commentary(parsed, payload, validation)
    if not ok:
        log.info("commentary.rejected", extra={"reason": reason})
        text = fallback_commentary(payload)
        return _pack(text, report, source="deterministic", mode="deterministic_fallback", provider_name=provider_name, model=model_name, diagnostic=reason)
    return _pack(parsed, report, source="llm", mode="llm", provider_name=str(result.get("provider") or provider_name), model=str(result.get("model") or model_name))


def commentary_input(report: dict[str, Any]) -> dict[str, Any]:
    """Bounded facts. Suppressed claims and rejected evidence are omitted."""
    gate = report.get("gate") or {}
    validation = report.get("gate_validation") or {}
    rules = []
    for item in gate.get("decision_rules") or []:
        if item.get("outcome") not in {None, "triggered"}:
            continue
        rules.append({
            "rule_id": item.get("rule_id"),
            "title": item.get("title"),
            "decision": item.get("decision"),
            "evidence_grade": item.get("evidence_grade"),
        })
    evidence = []
    for item in (report.get("evidence_catalog") or [])[:12]:
        evidence.append({
            "evidence_id": item.get("evidence_id"),
            "statement": _clip(item.get("statement") or "", 240),
            "source": item.get("source"),
            "file_path": item.get("file_path"),
            "service_id": item.get("service_id"),
        })
    inferences = []
    for item in (report.get("inferences") or [])[:8]:
        inferences.append({
            "inference_id": item.get("inference_id"),
            "type": item.get("type"),
            "statement": _clip(item.get("statement") or "", 240),
            "affected_services": list(item.get("affected_services") or [])[:6],
        })
    services: list[str] = []
    for name in (report.get("impact") or {}).get("affected_services") or []:
        if name and name not in services:
            services.append(str(name))
    for item in inferences:
        for name in item["affected_services"]:
            if name and name not in services:
                services.append(str(name))
    actions = report.get("actions") or []
    payload = {
        "decision": gate.get("decision") or "incomplete",
        "decision_presentation": {
            "heading": (report.get("decision_presentation") or {}).get("heading") or "",
            "label": (report.get("decision_presentation") or {}).get("label") or "",
        },
        "assessment": {
            "headline": (report.get("assessment") or {}).get("headline") or "",
            "summary": (report.get("assessment") or {}).get("summary") or "",
        },
        "gate_validation": {
            "status": validation.get("status") or "",
            "observed_decision": validation.get("observed_decision") or gate.get("decision"),
            "expected_decision": validation.get("expected_decision") or "",
            "summary": validation.get("summary") or "",
        },
        "triggered_rules": rules[:8],
        "evidence": evidence,
        "inferences": inferences,
        "affected_services": services[:8],
        "required_actions": [_action_view(item) for item in actions if item.get("required")][:6],
        "recommended_actions": [_action_view(item) for item in actions if not item.get("required")][:6],
        "limitations": [str(item) for item in (report.get("limitations") or [])[:6]],
        "repository": (report.get("change") or {}).get("repository") or "",
        "service_id": (report.get("change") or {}).get("service_id") or "",
    }
    return _redact_tree(payload)


def validate_commentary(text: dict[str, Any], payload: dict[str, Any], validation: dict[str, Any] | None = None) -> tuple[bool, str]:
    if not isinstance(text, dict):
        return False, "invalid_shape"
    if _FORBIDDEN_KEYS & set(text):
        return False, "forbidden_field"
    for field in _FIELDS:
        if field not in text or not isinstance(text[field], str):
            return False, "missing_field"
        if field != "limitation_note" and not text[field].strip():
            return False, "empty_field"
        if _too_long(text[field], **_LIMITS[field]):
            return False, "too_long"
    blob = "\n".join(text[field] for field in _FIELDS)
    if _FORBIDDEN_LANGUAGE.search(blob):
        return False, "forbidden_language"
    allowlist = json.dumps(payload, default=str)
    for pattern in _IDENTITY:
        for match in pattern.findall(blob):
            if match not in allowlist:
                return False, "unknown_identity"
    decision = str(payload.get("decision") or "")
    for label in ("PASS", "WARN", "BLOCK", "INCOMPLETE"):
        if label == decision.upper():
            continue
        if re.search(rf"\b{label}\b", blob):
            return False, "other_decision"
    status = str((validation or payload.get("gate_validation") or {}).get("status") or "")
    lowered = blob.lower()
    if status == "failed":
        if any(phrase in lowered for phrase in _OVERCLAIM) or "safe to proceed" in lowered:
            return False, "overclaim"
        if not any(phrase in lowered for phrase in ("could not", "validation failed", "not confirmed", "requires technical review")):
            return False, "missing_validation_failure"
    elif status == "validated_with_limitations":
        if any(phrase in lowered for phrase in ("fully validated", "all checks passed")):
            return False, "overclaim"
    return True, ""


def _temporal_files(payload: dict[str, Any]) -> list[str]:
    files = []
    for item in payload.get("evidence") or []:
        path = str(item.get("file_path") or "").strip()
        if path and path not in files and not path.startswith("/") and "\\" not in path:
            files.append(path)
    return files[:5]


def fallback_commentary(payload: dict[str, Any]) -> dict[str, str]:
    decision = str(payload.get("decision") or "incomplete")
    validation = payload.get("gate_validation") or {}
    if validation.get("status") == "failed":
        return _failure_copy()
    rules = [str(item.get("rule_id") or "") for item in payload.get("triggered_rules") or []]
    consumer = _named_service(payload)
    operation = _operation(payload)
    path = _known_path(payload)
    if decision == "block" and "BG-CONTRACT-001" in rules:
        removed = operation or "a contract"
        who = consumer or "a known consumer"
        provider = payload.get("service_id") or "The changed service"
        text = {
            "headline": "Restore consumed contract compatibility",
            "summary": f"{provider} removes {removed} while {who} still consumes that operation. Nami confirmed the current dependency and blocked the change.",
            "why_it_matters": "The consumer still relies on an operation that would no longer be provided.",
            "next_action": f"Restore the endpoint or update {who} in the same coordinated change, then rerun the review.",
            "evidence_comment": "The decision is supported by deterministic contract and dependency evidence.",
        }
    elif decision == "block" and "BG-VERIFY-001" in rules:
        text = {
            "headline": "Required verification failed",
            "summary": "A required verification command finished with a non-zero exit. Nami blocked the change.",
            "why_it_matters": "The configured verification did not pass.",
            "next_action": "Fix the failing verification and rerun the review.",
            "evidence_comment": "The decision is supported by the verification command result.",
        }
    elif decision == "block":
        text = {
            "headline": "A verified project rule matched",
            "summary": "Nami verified a blocking project rule and stopped the change.",
            "why_it_matters": "A verified rule requirement is satisfied.",
            "next_action": "Resolve the matched project rule and rerun the review.",
            "evidence_comment": "The decision is supported by deterministic policy evidence.",
        }
    elif decision == "incomplete" and "BG-GRAPH-001" in rules:
        text = {
            "headline": "Current dependency evidence is incomplete",
            "summary": "The published graph does not match the reviewed revision, so Nami cannot safely complete the impact decision.",
            "why_it_matters": "Nami lacks required evidence to safely finish the decision.",
            "next_action": "Refresh intelligence or run an Exact review.",
            "evidence_comment": "The published graph alignment does not support a completed decision.",
        }
    elif decision == "incomplete":
        text = {
            "headline": "Required evidence is unavailable",
            "summary": "A required check did not finish, so Nami cannot complete the decision.",
            "why_it_matters": "Nami lacks required evidence to safely finish the decision.",
            "next_action": "Run the missing check, then review the change again.",
            "evidence_comment": "The decision is incomplete because required evidence is unavailable.",
        }
    elif decision == "warn" and "BG-IMPACT-001" in rules:
        target = consumer or "the downstream service"
        review_path = path or target
        text = {
            "headline": f"Review {target} before deployment" if consumer else "Review the downstream service",
            "summary": f"The changed component reaches {target} through a confirmed dependency, but Nami found no verified breaking contract.",
            "why_it_matters": "Impact evidence exists, but no verified blocking condition was established.",
            "next_action": f"Review {review_path} and run its mapped verification.",
            "evidence_comment": "The warning is supported by current dependency evidence.",
        }
    elif decision == "warn" and "BG-TEMPORAL-001" in rules:
        files = _temporal_files(payload)
        named = files[0] if len(files) == 1 else f"{files[0]} and {len(files) - 1} other files" if files else ""
        text = {
            "headline": "Historical association needs review",
            "summary": "This is a historical association. It is not a blocking condition.",
            "why_it_matters": "This evidence alone does not confirm a current structural dependency."
            + (f" The associated file is {named}." if named else ""),
            "next_action": f"Review {named} before deployment." if named else "Review the associated files before deployment.",
            "evidence_comment": f"Repository history identified {files[0]} as a temporal review target." if files else "Repository history identified the associated files as temporal review targets.",
        }
    elif decision == "pass":
        text = {
            "headline": "No verified blocker was found",
            "summary": "Required checks that were available passed, and Nami found no consumed breaking contract or blocking policy violation.",
            "why_it_matters": "This change is ready to proceed under the current Gate policy.",
            "next_action": "Proceed under the current Gate policy.",
            "evidence_comment": "No verified blocking evidence was recorded.",
        }
    else:
        text = {
            "headline": "Review is recommended",
            "summary": "Nami found review evidence and no verified blocking condition.",
            "why_it_matters": "Impact or review evidence exists, but no verified blocking condition was established.",
            "next_action": "Review the listed findings before proceeding.",
            "evidence_comment": "The warning is supported by the stored review evidence.",
        }
    text["validation_comment"] = _validation_sentence(validation)
    text["limitation_note"] = _limitation_sentence(payload, validation)
    if not validate_commentary(text, payload, validation)[0]:
        return _generic(payload)
    return text


def _failure_copy() -> dict[str, str]:
    return {
        "headline": "Gate decision requires technical review",
        "summary": "The stored Gate result could not be independently reproduced from the available evidence and rule requirements.",
        "why_it_matters": "A decision that cannot be replayed should not be treated as confirmed.",
        "next_action": "Do not rely on this decision until the validation mismatch is reviewed.",
        "evidence_comment": "The stored evidence did not confirm the recorded Gate decision.",
        "validation_comment": "Independent validation failed, so this result is not confirmed.",
        "limitation_note": "",
    }


def _generic(report_or_payload: dict[str, Any]) -> dict[str, str]:
    decision = str((report_or_payload.get("gate") or {}).get("decision") or report_or_payload.get("decision") or "incomplete")
    validation = report_or_payload.get("gate_validation") or {}
    if validation.get("status") == "failed" or decision == "incomplete" and validation.get("status") == "failed":
        return _failure_copy()
    if validation.get("status") == "failed":
        return _failure_copy()
    text = {
        "headline": "Review the stored Gate result",
        "summary": "Nami recorded a Gate decision from the stored evidence.",
        "why_it_matters": "Use the decision, evidence, and validation details together.",
        "next_action": "Read the required actions before proceeding.",
        "evidence_comment": "The explanation uses the stored Nami evidence.",
        "validation_comment": _validation_sentence(validation),
        "limitation_note": "",
    }
    return text


def _validation_sentence(validation: dict[str, Any]) -> str:
    status = validation.get("status")
    if status == "failed":
        return "Independent validation failed, so this result is not confirmed."
    if status == "validated_with_limitations":
        return "The Gate decision was reproduced, but an optional validation check could not be completed."
    if status == "validated":
        return "The Gate decision was independently replayed and reproduced successfully."
    return "Gate validation was not recorded for this review."


def _limitation_sentence(payload: dict[str, Any], validation: dict[str, Any]) -> str:
    if validation.get("status") != "validated_with_limitations":
        return ""
    limitations = payload.get("limitations") or []
    if limitations:
        sentence = str(limitations[0]).strip()
        if sentence and not sentence.endswith("."):
            sentence = f"{sentence}."
        if not _too_long(sentence, **_LIMITS["limitation_note"]):
            return sentence
    return "An optional validation check could not be completed."


def _pack(text: dict[str, str], report: dict[str, Any], *, source: str, mode: str, provider_name: str, model: str, diagnostic: str = "") -> dict[str, Any]:
    stamp = now()
    body = {field: str(text.get(field) or "") for field in _FIELDS}
    body.update({
        "advisory": True,
        "source": source,
        "mode": mode,
        "purpose": COMMENTARY_PURPOSE,
        "provider": provider_name,
        "model": model,
        "generated_at": stamp,
        "commentary_generated_at": stamp,
    })
    if diagnostic:
        body["model_diagnostic"] = diagnostic
    body["gate_decision"] = (report.get("gate") or {}).get("decision")
    return body


def _action_view(item: dict[str, Any]) -> dict[str, str]:
    return {"title": str(item.get("title") or ""), "description": _clip(str(item.get("description") or ""), 240)}


def _named_service(payload: dict[str, Any]) -> str:
    for name in payload.get("affected_services") or []:
        if name:
            return str(name)
    return ""


def _operation(payload: dict[str, Any]) -> str:
    blob = json.dumps(payload, default=str)
    match = re.search(r"\b(?:GET|POST|PUT|PATCH|DELETE)\s+/[A-Za-z0-9_./{}-]+", blob)
    return match.group(0) if match else ""


def _known_path(payload: dict[str, Any]) -> str:
    blob = json.dumps(payload, default=str)
    match = re.search(r"\b[\w./-]+\.(?:js|jsx|ts|tsx|py)\b", blob)
    return match.group(0) if match else ""


def _too_long(text: str, *, chars: int, sentences: int, words: int) -> bool:
    if len(text) > chars or len(text.split()) > words:
        return True
    parts = [part for part in re.split(r"[.!?]+(?=\s|$)", text) if part.strip()]
    return len(parts) > sentences


def _clip(text: str, limit: int) -> str:
    cleaned = redact(text).replace("\n", " ").strip()
    return cleaned[:limit]


def _redact_tree(value: Any) -> Any:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        return [_redact_tree(item) for item in value]
    if isinstance(value, dict):
        return {key: _redact_tree(item) for key, item in value.items()}
    return value
