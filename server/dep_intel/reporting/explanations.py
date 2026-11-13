"""Deterministic English explanations for stored report evidence.

These sentences describe why a stored file, symbol, contract, or edge is present.
They do not change ranking, Gate decisions, or scores.
"""

from __future__ import annotations

from typing import Any


def explain_relationship(row: dict[str, Any]) -> str:
    """One sentence: who calls what, and why the edge is trusted."""
    consumer = str(row.get("from_service") or "consumer")
    provider = str(row.get("to_service") or "provider")
    contract = str(row.get("contract") or "contract")
    caller = str(row.get("caller_symbol") or "")
    file_path = str(row.get("caller_file") or "")
    kind = str(row.get("kind") or "http")
    verb = "calls" if kind == "grpc" else "publishes" if kind == "event" else "consumes"
    if caller and file_path:
        actor = f"{caller} in {file_path} {verb} {contract}"
    elif caller:
        actor = f"{caller} {verb} {contract}"
    elif file_path:
        actor = f"{file_path} {verb} {contract}"
    else:
        actor = f"{consumer} {verb} {contract}"
    return f"{consumer} → {provider}: {actor}. {relationship_basis(row)}"


def relationship_basis(row: dict[str, Any]) -> str:
    provenance = str(row.get("provenance") or "").lower()
    if provenance == "declared":
        return "Declared edge, not history."
    if provenance == "openapi":
        return "API spec, not history."
    return "Contract edge, not history."


def explain_contract(provider: str, contract: str, consumers: list[str]) -> str:
    names = ", ".join(item for item in consumers if item) or "none"
    return f"{provider or 'Provider'} provides {contract or 'contract'} for {names}."


def explain_unresolved(row: dict[str, Any]) -> str:
    service = str(row.get("service") or "Service")
    name = str(row.get("name") or row.get("host") or "provider")
    return f"{service} references {name}. Unresolved, not a dependency."


def explain_service(service_id: str, *, provides: int, upstream: list[str], downstream: list[str]) -> str:
    del provides
    upstream_text = ", ".join(upstream) if upstream else "none"
    downstream_text = ", ".join(downstream) if downstream else "none"
    return f"{service_id}: contract edges. Upstream {upstream_text}. Downstream {downstream_text}."


def explain_review_entity(item: dict[str, Any], *, temporal_review: bool = False) -> dict[str, str]:
    """Why one review target was selected, separate from the review-level summary."""
    file_path = str(item.get("file_path") or item.get("file") or "")
    symbol = str(item.get("symbol") or item.get("name") or "")
    contract = str(item.get("contract") or item.get("qualified_name") or "")
    service = str(item.get("service_id") or item.get("service") or "")
    label = file_path or symbol or contract or service or "This item"
    path = _path(item)
    path_text = _path_sentence(path)
    tag = _tag(item, path_text, temporal_review=temporal_review)
    entity = "api" if contract and not file_path else "symbol" if symbol and file_path else "file" if file_path else "service"
    if tag == "temporal_only":
        why = "History associates it with the change."
        basis = "Repository history."
        claim = "Temporal target, not a dependency."
        inspect = f"Check {label}."
    elif tag == "hybrid":
        why = "Graph path and history both match."
        basis = path_text or "Graph and history."
        claim = "Hybrid target."
        inspect = f"Check {label}."
    else:
        why = f"Uses {contract}." if contract and file_path else "Stored graph reaches it."
        basis = path_text or "Structural graph."
        claim = "Structural target."
        inspect = f"Check {label}."
    text = basis if path_text and tag != "temporal_only" else f"{why} {claim}"
    return {
        "entity": entity,
        "kind": tag,
        "label": label,
        "service": service,
        "explanation": text,
        "why_selected": why,
        "why_tagged": why,
        "evidence_basis": basis,
        "claim": claim,
        "inspect": inspect,
    }


def explain_cross_cluster(service: str) -> dict[str, str]:
    why = "Reached by the review, outside this cluster."
    return {
        "entity": "service",
        "kind": "cross_cluster_target",
        "label": service,
        "service": service,
        "explanation": "Outside this cluster.",
        "why_selected": why,
        "why_tagged": why,
        "evidence_basis": "Stored review target.",
        "claim": "Not a cluster member.",
        "inspect": "Open the full review.",
    }


def _tag(item: dict[str, Any], path_text: str, *, temporal_review: bool = False) -> str:
    origin = str(item.get("evidence_origin") or "")
    structured = item.get("structured") if isinstance(item.get("structured"), dict) else {}
    origin = origin or str(structured.get("evidence_origin") or "")
    source = str(item.get("source") or "").lower()
    if origin == "temporal_only" or source == "git_history":
        return "temporal_only"
    if origin in {"hybrid", "both"}:
        return "hybrid"
    if origin == "structural_only" or path_text or source in {"crg", "cluster_graph", "nrm"}:
        return "structural_review_target"
    if temporal_review:
        return "temporal_only"
    return "structural_review_target"


def _path(item: dict[str, Any]) -> list[dict[str, Any]]:
    structural = item.get("structural") if isinstance(item.get("structural"), dict) else {}
    direct = structural.get("path")
    if isinstance(direct, list):
        return [step for step in direct if isinstance(step, dict)]
    evidence = item.get("evidence")
    if isinstance(evidence, list):
        for entry in evidence:
            if not isinstance(entry, dict):
                continue
            data = entry.get("data") if isinstance(entry.get("data"), dict) else {}
            nested = data.get("path")
            if isinstance(nested, list):
                return [step for step in nested if isinstance(step, dict)]
    return []


def has_named_path(item: dict[str, Any]) -> bool:
    return bool(_path_sentence(_path(item)))


def _path_sentence(path: list[dict[str, Any]]) -> str:
    names: list[str] = []
    for step in path:
        for key in ("source_name", "target_name"):
            short = _short_name(str(step.get(key) or ""))
            if short and (not names or names[-1] != short):
                names.append(short)
    if len(names) < 2:
        return ""
    return " → ".join(names)


def _short_name(value: str) -> str:
    text = value.strip()
    if not text:
        return ""
    parts = text.split(":")
    method = next((part for part in parts if part in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"}), "")
    route = next((part for part in parts if part.startswith("/")), "")
    if method and route:
        return f"{method} {route}"
    if "::" in text:
        return text.rsplit("::", 1)[-1]
    return text
