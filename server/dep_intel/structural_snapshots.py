"""Structural snapshot providers for Nami Rank Module (NRM).

Interactive analysis uses the published cluster snapshot. A historical cutoff
does not rebuild CRG. ``historically_aligned`` stays false until an evaluation
snapshot is supplied.

A later evaluation snapshot is built by resolving each repository SHA at the
prediction time, checking out those SHAs, building CRG databases, assembling
the cluster snapshot, and persisting the revision vector. This module does not
perform that checkout.
"""

from __future__ import annotations

from typing import Protocol

from dep_intel.graph_models import Snapshot


class StructuralSnapshotProvider(Protocol):
    def current(self) -> Snapshot | None: ...

    def at_cutoff(self, timestamp: str) -> Snapshot | None: ...

    @property
    def historically_aligned(self) -> bool: ...


class CurrentStructuralSnapshotProvider:
    """The live cluster snapshot. Not a frozen historical topology."""

    historically_aligned = False

    def __init__(self, snapshot: Snapshot | None) -> None:
        self._snapshot = snapshot

    def current(self) -> Snapshot | None:
        return self._snapshot

    def at_cutoff(self, timestamp: str) -> Snapshot | None:
        return None
