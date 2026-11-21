"""Review scope is the allowed impact boundary for one Boundary Gate run.

Repository scope is the historical default. Service-cluster scope limits which
candidate services may enter the same gate. It does not create another gate.
"""

from __future__ import annotations

import re
from typing import Any

from dep_intel.config import Settings
from dep_intel.graph_models import Snapshot
from dep_intel.service_clusters import (
    cluster_downstream,
    cluster_label,
    detect_service_clusters,
    get_service_cluster,
    membership_source_label,
)

SUPPORTED_REVIEW_SCOPES = {"repository", "service_cluster"}
_PR = re.compile(r"github\.com[:/](?P<owner>[^/]+)/(?P<repo>[^/#?]+?)(?:\.git)?/pull/(?P<number>\d+)", re.IGNORECASE)


class ReviewScopeError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def normalize_review_scope(value: str | None) -> str:
    mode = str(value or "repository").strip() or "repository"
    if mode not in SUPPORTED_REVIEW_SCOPES:
        raise ReviewScopeError("invalid_review_scope", "Review scope must be repository or service_cluster.")
    return mode


def ensure_cluster_reference(snapshot: Snapshot | None, review_scope: str | None, cluster_id: str | None) -> None:
    """Reject a cluster review before any gate work when the cluster cannot be resolved."""
    mode = normalize_review_scope(review_scope)
    if mode != "service_cluster":
        return
    if snapshot is None:
        raise ReviewScopeError("cluster_snapshot_unavailable", "No cluster snapshot is available for a service cluster review.")
    selected = str(cluster_id or "").strip()
    if not selected:
        raise ReviewScopeError("cluster_required", "Choose a service cluster.")
    if get_service_cluster(snapshot, selected) is None:
        raise ReviewScopeError("unknown_cluster", "The selected service cluster is not in the published snapshot.")


def resolve_review_scope(
    snapshot: Snapshot | None,
    review_scope: str | None,
    cluster_id: str | None,
    origin_service_id: str | None,
) -> dict[str, Any]:
    """Bound one review to a repository or one declared or detected cluster."""
    mode = normalize_review_scope(review_scope)
    origin = str(origin_service_id or "")
    if mode == "repository":
        return {
            "mode": "repository",
            "label": "Individual repository",
            "origin_service_id": origin,
        }
    if snapshot is None:
        raise ReviewScopeError("cluster_snapshot_unavailable", "No cluster snapshot is available for a service cluster review.")
    selected = str(cluster_id or "").strip()
    if not selected:
        raise ReviewScopeError("cluster_required", "Choose a service cluster.")
    groups = detect_service_clusters(snapshot)
    cluster = get_service_cluster(snapshot, selected)
    if cluster is None:
        raise ReviewScopeError("unknown_cluster", "The selected service cluster is not in the published snapshot.")
    members = [str(item) for item in cluster.get("services") or [] if item]
    if origin not in set(members):
        raise ReviewScopeError("origin_not_in_cluster", "The change origin is not a member of the selected service cluster.")
    source = str(cluster.get("membership_source") or groups.get("membership_source") or "derived")
    return {
        "mode": "service_cluster",
        "label": "Service Cluster",
        "cluster_id": str(cluster.get("cluster_id") or selected),
        "cluster_label": display_cluster_label(cluster),
        "membership_source": source,
        "membership_source_label": membership_source_label(source),
        "origin_service_id": origin,
        "member_services": members,
    }


def display_cluster_label(cluster: dict[str, Any]) -> str:
    """Use the declared cluster name. Derived clusters keep the relationship label."""
    if str(cluster.get("membership_source") or "") == "declared":
        return _readable_cluster_name(str(cluster.get("cluster_id") or cluster.get("label") or ""))
    stored = str(cluster.get("label") or cluster.get("cluster_id") or "")
    pretty = cluster_label([str(item) for item in cluster.get("services") or [] if item])
    if pretty.endswith("service cluster"):
        return pretty
    return stored or pretty


def _readable_cluster_name(value: str) -> str:
    text = value.replace("_", " ").strip()
    if not text:
        return text
    return text[:1].upper() + text[1:]


def confirm_pull_request_origin(
    scope: dict[str, Any],
    snapshot: Snapshot | None,
    settings: Settings,
    resolved: dict[str, Any] | None,
    source: dict[str, Any] | None,
) -> dict[str, Any]:
    """Keep the selected cluster. A pull request from outside it is rejected."""
    if scope.get("mode") != "service_cluster":
        return scope
    kind = str((resolved or {}).get("kind") or (source or {}).get("kind") or "")
    if kind != "github_pr":
        return scope
    pr_service = pull_request_service(settings, snapshot, resolved or {}, source or {})
    origin = str(scope.get("origin_service_id") or "")
    members = set(scope.get("member_services") or [])
    if pr_service and origin and pr_service != origin:
        raise ReviewScopeError("origin_not_in_cluster", "The pull request does not belong to the selected change origin.")
    if pr_service and pr_service not in members:
        raise ReviewScopeError("origin_not_in_cluster", "The pull request is outside the selected service cluster.")
    if not origin and pr_service:
        return resolve_review_scope(snapshot, "service_cluster", str(scope.get("cluster_id") or ""), pr_service)
    if not origin and not pr_service:
        raise ReviewScopeError("origin_not_in_cluster", "The pull request could not be matched to the selected service cluster.")
    return scope


def pull_request_service(settings: Settings, snapshot: Snapshot | None, resolved: dict[str, Any], source: dict[str, Any]) -> str:
    from dep_intel.repository_identity import partition_service

    pr = resolved.get("pr") if isinstance(resolved.get("pr"), dict) else {}
    url = str(source.get("pr_url") or pr.get("url") or "")
    match = _PR.search(url.strip())
    if match is None:
        return ""
    name = match.group("repo")
    owner = match.group("owner")
    for requested in (name, f"{owner}/{name}", f"{owner}_{name}"):
        found = partition_service(settings, snapshot, requested)
        if found:
            return found
    return ""


def present_review_scope(scope: dict[str, Any] | None, impact: dict[str, Any] | None, findings: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Presentation metadata. Absence of impact evidence is not a safety claim."""
    chosen = scope if isinstance(scope, dict) else {}
    origin = str(chosen.get("origin_service_id") or "")
    if chosen.get("mode") != "service_cluster":
        return {
            "mode": "repository",
            "label": "Individual repository",
            "origin_service_id": origin,
        }
    members = [str(item) for item in chosen.get("member_services") or [] if item]
    member_set = set(members)
    evidenced = _evidenced_members(impact or {}, findings or [], member_set)
    if origin and origin in member_set:
        evidenced.add(origin)
    affected = [item for item in members if item in evidenced]
    unaffected = [item for item in members if item not in evidenced]
    outside = list((impact or {}).get("out_of_scope_targets") or [])
    return {
        "mode": "service_cluster",
        "label": "Service Cluster",
        "cluster_id": str(chosen.get("cluster_id") or ""),
        "cluster_label": str(chosen.get("cluster_label") or ""),
        "membership_source": str(chosen.get("membership_source") or ""),
        "membership_source_label": str(chosen.get("membership_source_label") or ""),
        "origin_service_id": origin,
        "member_count": len(members),
        "member_services": members,
        "affected_member_count": len(affected),
        "affected_member_services": affected,
        "unaffected_member_count": len(unaffected),
        "unaffected_member_services": unaffected,
        "unaffected_note": "No evidenced impact found",
        "out_of_scope_targets": outside,
    }


def apply_impact_boundary(ranked: list[dict[str, Any]], scope: dict[str, Any] | None) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Drop candidates outside the selected cluster. Keep them as advisory metadata."""
    if not scope or scope.get("mode") != "service_cluster":
        return ranked, []
    allowed = set(scope.get("member_services") or [])
    kept: list[dict[str, Any]] = []
    outside: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in ranked:
        service = str(item.get("service_id") or "")
        if service and service in allowed:
            kept.append(item)
            continue
        if service and service not in seen:
            seen.add(service)
            outside.append({"service_id": service, "reason": "Outside selected Service Cluster"})
    for index, item in enumerate(kept, start=1):
        item["rank"] = index
    return kept, outside


def out_of_scope_note(targets: list[dict[str, Any]] | None) -> str:
    count = len(targets or [])
    if count == 0:
        return ""
    if count == 1:
        return (
            "1 evidenced target exists outside the selected Service Cluster. "
            "Run an Individual Repository review if broader cross-cluster impact must be evaluated."
        )
    return (
        f"{count} evidenced targets exist outside the selected Service Cluster. "
        "Run an Individual Repository review if broader cross-cluster impact must be evaluated."
    )


def scoped_downstream(snapshot: Snapshot | None, service_id: str, scope: dict[str, Any] | None) -> dict[str, Any]:
    cluster_id = ""
    if scope and scope.get("mode") == "service_cluster":
        cluster_id = str(scope.get("cluster_id") or "")
    return cluster_downstream(snapshot, service_id, cluster_id=cluster_id)


def _evidenced_members(impact: dict[str, Any], findings: list[dict[str, Any]], members: set[str]) -> set[str]:
    evidenced: set[str] = set()
    for service in impact.get("affected_services") or []:
        if service in members:
            evidenced.add(str(service))
    for candidate in impact.get("ranked_candidates") or []:
        service = str(candidate.get("service_id") or "")
        if service in members:
            evidenced.add(service)
    for finding in findings:
        service = str(finding.get("service_id") or "")
        if service in members:
            evidenced.add(service)
        structured = finding.get("structured") if isinstance(finding.get("structured"), dict) else {}
        for consumer in structured.get("consumers") or []:
            if isinstance(consumer, dict):
                name = str(consumer.get("service_id") or "")
            else:
                name = str(consumer or "")
            if name in members:
                evidenced.add(name)
    return evidenced
