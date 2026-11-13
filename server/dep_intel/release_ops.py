"""Release-loop views over stored reviews, snapshots, and health artifacts.

Does not rank, score, or change a Gate decision.
"""

from __future__ import annotations

import fnmatch
import json
import os
import urllib.request
from pathlib import Path
from typing import Any

from dep_intel.config import Settings
from dep_intel.graph_models import UNRESOLVED, Snapshot
from dep_intel.service_clusters import confirmed_relationships
from dep_intel.store import read_json


def check_summary(report: dict[str, Any]) -> dict[str, Any]:
    """Text and exit code for a pull-request check. Warn stays green. Block fails."""
    gate = report.get("gate") if isinstance(report.get("gate"), dict) else {}
    decision = str(gate.get("decision") or "incomplete").lower()
    commentary = report.get("commentary") if isinstance(report.get("commentary"), dict) else {}
    change = report.get("change") if isinstance(report.get("change"), dict) else {}
    impact = report.get("impact") if isinstance(report.get("impact"), dict) else {}
    service = str(change.get("service_id") or report.get("service") or "")
    contracts = []
    for item in (change.get("contract_changes") or [])[:8]:
        if isinstance(item, dict):
            contracts.append(str(item.get("qualified_name") or item.get("contract") or item.get("kind") or "contract"))
    affected = [str(item) for item in (impact.get("affected_services") or []) if item][:12]
    headline = str(commentary.get("headline") or "")
    if not headline or headline.lower() == decision:
        headline = str(commentary.get("summary") or decision)
    lines = [
        f"Nami Gate: {decision.upper()} {service}".strip(),
        headline,
        f"Contracts: {', '.join(contracts) if contracts else 'none recorded'}",
        f"Affected services: {', '.join(affected) if affected else 'none recorded'}",
        f"Review: {report.get('review_id') or ''}",
    ]
    return {
        "decision": decision,
        "exit_code": 1 if decision == "block" else 0,
        "text": "\n".join(lines),
        "service": service,
        "contracts": contracts,
        "affected_services": affected,
    }


def notification_for(report: dict[str, Any]) -> dict[str, str]:
    summary = check_summary(report)
    return {
        "decision": summary["decision"],
        "text": summary["text"].replace("\n", " "),
        "review_id": str(report.get("review_id") or ""),
    }


def deliver_notification(settings: Settings, report: dict[str, Any]) -> dict[str, Any]:
    """Append the one-line notice locally. Post it only when a webhook is configured."""
    notice = notification_for(report)
    path = settings.output_dir / "notifications.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(notice) + "\n")
    webhook = os.environ.get("NAMI_NOTIFY_WEBHOOK") or ""
    delivered = "stored"
    if webhook.startswith("https://") and notice["decision"] == "block":
        body = json.dumps({"text": notice["text"]}).encode("utf-8")
        request = urllib.request.Request(webhook, data=body, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                delivered = f"webhook {response.status}"
        except OSError:
            delivered = "webhook failed"
    return {**notice, "delivered": delivered}


def match_owner(path: str, rules: list[tuple[str, str]]) -> str:
    """Last matching CODEOWNERS rule wins. Empty when nothing matches."""
    relative = path.lstrip("./")
    found = ""
    for pattern, owners in rules:
        if _owner_match(pattern, relative):
            found = owners
    return found


def parse_codeowners(text: str) -> list[tuple[str, str]]:
    rules: list[tuple[str, str]] = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        rules.append((parts[0], " ".join(parts[1:])))
    return rules


def owners_for_review(settings: Settings, report: dict[str, Any]) -> dict[str, Any]:
    checkout = _checkout(settings, report)
    rules: list[tuple[str, str]] = []
    source = ""
    if checkout is not None:
        for candidate in (checkout / "CODEOWNERS", checkout / ".github" / "CODEOWNERS"):
            if candidate.is_file():
                rules = parse_codeowners(candidate.read_text(encoding="utf-8", errors="replace"))
                source = str(candidate.relative_to(checkout))
                break
    rows = []
    for path, service in _review_files(report):
        rows.append({"file": path, "service": service, "owner": match_owner(path, rules) if rules else ""})
    return {
        "status": "available" if rules else "not_recorded",
        "source": source,
        "items": rows[:40],
    }


def load_deployments(settings: Settings) -> dict[str, Any]:
    payload = read_json(settings.output_dir, "deployments.json")
    if not isinstance(payload, dict) or not payload.get("services"):
        return {"status": "not_recorded", "environment": "", "services": [], "note": "No deployed revision is recorded."}
    services = payload.get("services") if isinstance(payload.get("services"), dict) else {}
    rows = [
        {"service": service, "sha": str((item or {}).get("sha") or ""), "at": str((item or {}).get("at") or "")}
        for service, item in services.items()
        if isinstance(item, dict)
    ]
    return {
        "status": "recorded",
        "environment": str(payload.get("environment") or "production"),
        "services": rows,
        "note": "Deployed revisions are recorded separately from the source snapshot.",
    }


def deployment_note(settings: Settings, snapshot: Snapshot | None, report: dict[str, Any]) -> str:
    recorded = load_deployments(settings)
    if recorded["status"] != "recorded" or snapshot is None:
        return recorded["note"]
    by_service = {item["service"]: item["sha"] for item in recorded["services"]}
    change = report.get("change") if isinstance(report.get("change"), dict) else {}
    provider = str(change.get("service_id") or "")
    contracts = change.get("contract_changes") if isinstance(change.get("contract_changes"), list) else []
    contract = ""
    if contracts and isinstance(contracts[0], dict):
        contract = str(contracts[0].get("qualified_name") or contracts[0].get("contract") or "")
    impact = report.get("impact") if isinstance(report.get("impact"), dict) else {}
    consumers = [str(item) for item in (impact.get("affected_services") or []) if item and item != provider]
    provider_sha = by_service.get(provider) or _head(snapshot, provider)
    consumer = consumers[0] if consumers else ""
    consumer_sha = by_service.get(consumer) or ""
    environment = recorded["environment"]
    if provider and contract and consumer and provider_sha and consumer_sha:
        return f"{provider} at {provider_sha} provides {contract}; {consumer} in {environment} is at {consumer_sha}."
    if provider and provider_sha:
        return f"{provider} deployed sha {provider_sha} in {environment}."
    return recorded["note"]


def hygiene_rows(settings: Settings, snapshot: Snapshot | None) -> dict[str, Any]:
    updates = read_json(settings.output_dir, "package_updates.json")
    matrix = read_json(settings.output_dir, "dep_matrix.json")
    scanned = _scanned_repos(matrix, updates)
    available = _update_rows(updates)
    if snapshot is None:
        return {"status": "not_scanned", "rows": [], "note": "No snapshot is published."}
    providers = {row["to_service"] for row in confirmed_relationships(snapshot)}
    rows = []
    for service_id, part in sorted(snapshot.partitions.items()):
        names = _repo_names(part.repository, service_id)
        matched = [item for item in available if _repo_names(str(item.get("repo") or "")) & names]
        known = bool(names & scanned) or bool(matched)
        if not known:
            rows.append(_hygiene(service_id, part.repository, "not_scanned", "", "", "", False))
            continue
        if not matched:
            rows.append(_hygiene(service_id, part.repository, "current", "", "", "", service_id in providers))
            continue
        for item in matched:
            rows.append(_hygiene(
                service_id,
                str(item.get("repo") or part.repository),
                str(item.get("kind") or "update"),
                str(item.get("package") or ""),
                str(item.get("from") or ""),
                str(item.get("to") or ""),
                service_id in providers,
            ))
    return {
        "status": "available" if scanned or available else "not_scanned",
        "rows": rows,
        "note": "Not scanned means the repository is absent from the stored package check.",
    }


def drift_since_last_report(settings: Settings, snapshot: Snapshot | None) -> dict[str, Any]:
    from dep_intel.reporting.store import list_reports, read_report

    previous = next((item for item in list_reports(settings.output_dir, 50) if item.get("scope") in {"workspace", "cluster"}), None)
    if previous is None or snapshot is None:
        return {"status": "unavailable", "note": "No earlier workspace report is stored.", "added": [], "removed": [], "unresolved_delta": None, "stale": []}
    document = read_report(settings.output_dir, str(previous["report_id"])) or {}
    stored = ((document.get("relationships") or {}).get("items") or (document.get("relationships") or {}).get("relationships") or [])
    current = confirmed_relationships(snapshot)
    added, removed = _edge_delta(stored, current)
    previous_unresolved = int(((document.get("code_graph_diagnostics") or {}).get("unresolved_symbol_count") or 0))
    current_unresolved = sum(1 for node in snapshot.nodes.values() if node.category == UNRESOLVED)
    previous_stale = {str(item.get("service")) for item in (document.get("services") or []) if item.get("graph_status") == "stale"}
    current_stale = {service_id for service_id, part in snapshot.partitions.items() if part.quality != "ok" or part.error}
    return {
        "status": "available",
        "compared_to": previous["report_id"],
        "note": "Compared with the latest stored workspace report. Ranking was not rerun.",
        "added": added[:20],
        "removed": removed[:20],
        "added_count": len(added),
        "removed_count": len(removed),
        "unresolved_delta": current_unresolved - previous_unresolved,
        "stale": sorted(current_stale - previous_stale)[:20],
    }


def _hygiene(service: str, repository: str, status: str, package: str, current: str, fixed: str, reachable: bool) -> dict[str, Any]:
    return {
        "service": service,
        "repository": repository,
        "status": status,
        "package": package,
        "current": current,
        "fixed": fixed,
        "reachable": reachable,
    }


def _edge_delta(previous: list[Any], current: list[dict[str, Any]]) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    def key(row: dict[str, Any]) -> tuple[str, str, str]:
        return (str(row.get("from_service") or ""), str(row.get("to_service") or ""), str(row.get("contract") or ""))

    def brief(row: dict[str, Any]) -> dict[str, str]:
        return {"from_service": str(row.get("from_service") or ""), "to_service": str(row.get("to_service") or ""), "contract": str(row.get("contract") or "")}

    old = {key(item): item for item in previous if isinstance(item, dict)}
    new = {key(item): item for item in current}
    added = [brief(new[item]) for item in new.keys() - old.keys()]
    removed = [brief(old[item]) for item in old.keys() - new.keys()]
    added.sort(key=lambda item: (item["from_service"], item["contract"]))
    removed.sort(key=lambda item: (item["from_service"], item["contract"]))
    return added, removed


def _scanned_repos(matrix: Any, updates: Any) -> set[str]:
    names: set[str] = set()
    repos = matrix.get("repositories") if isinstance(matrix, dict) else None
    if isinstance(repos, dict):
        for name in repos:
            names.update(_repo_names(str(name)))
    for item in _update_rows(updates):
        names.update(_repo_names(str(item.get("repo") or "")))
    names.discard("")
    return names


def _update_rows(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    buckets = payload.get("updates")
    if not isinstance(buckets, dict):
        return []
    rows = []
    for kind, items in buckets.items():
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict):
                rows.append({**item, "kind": kind})
    return rows


def _repo_names(*values: str) -> set[str]:
    names: set[str] = set()
    for value in values:
        text = (value or "").strip()
        if not text:
            continue
        names.add(text)
        names.add(text.split("/")[-1])
        names.add(Path(text).name)
    return names


def _checkout(settings: Settings, report: dict[str, Any]) -> Path | None:
    from dep_intel.repository_identity import load_index

    change = report.get("change") if isinstance(report.get("change"), dict) else {}
    repository = str(change.get("repository") or report.get("repository") or "")
    service = str(change.get("service_id") or "")
    record = load_index(settings).resolve(service_id=service, repository=repository, checkout=repository)
    if record and record.checkout and Path(record.checkout).is_dir():
        return Path(record.checkout)
    return None


def _review_files(report: dict[str, Any]) -> list[tuple[str, str]]:
    change = report.get("change") if isinstance(report.get("change"), dict) else {}
    service = str(change.get("service_id") or "")
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    candidates: list[Any] = list(report.get("changed_files") or [])
    candidates.extend(report.get("findings") or [])
    for item in candidates:
        path = ""
        item_service = service
        if isinstance(item, str):
            path = item
        elif isinstance(item, dict):
            path = str(item.get("file_path") or item.get("new_path") or item.get("path") or item.get("old_path") or "")
            item_service = str(item.get("service_id") or service)
        if not path or path in seen or path.startswith("/") or ".." in path.split("/"):
            continue
        seen.add(path)
        found.append((path, item_service))
    return found


def _head(snapshot: Snapshot, service_id: str) -> str:
    part = snapshot.partitions.get(service_id)
    return part.workspace_head if part else ""


def _owner_match(pattern: str, path: str) -> bool:
    token = pattern.lstrip("/")
    if token in {"*", "*/*"}:
        return True
    if token.endswith("/"):
        return path.startswith(token)
    if token.endswith("/**"):
        prefix = token[:-3]
        return path == prefix or path.startswith(prefix + "/")
    return fnmatch.fnmatch(path, token) or fnmatch.fnmatch(path, f"**/{token}")
