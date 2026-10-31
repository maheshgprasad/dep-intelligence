"""One confirmed service cluster. Membership is recalculated on the server."""

from __future__ import annotations

from typing import Any

from dep_intel.config import Settings
from dep_intel.graph_models import Snapshot
from dep_intel.reporting.scopes.workspace import build_workspace_document
from dep_intel.service_clusters import get_service_cluster


def build_service_cluster_document(settings: Settings, snapshot: Snapshot, cluster_id: str) -> dict[str, Any]:
    cluster = get_service_cluster(snapshot, cluster_id)
    if cluster is None:
        raise KeyError(cluster_id)
    return build_workspace_document(settings, snapshot, members=set(cluster["services"]), cluster=cluster)
