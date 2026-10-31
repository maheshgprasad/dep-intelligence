"""Compatibility import. Stored and requested scope "cluster" means the workspace."""

from dep_intel.reporting.scopes.workspace import build_workspace_document as build_cluster_document

__all__ = ["build_cluster_document"]
