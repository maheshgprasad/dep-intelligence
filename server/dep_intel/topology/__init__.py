"""Deterministic service-topology discovery.

Route names alone never choose a provider. A relative client URL stays
unresolved unless a host, alias, or compatibility binding identifies one service.
"""

from dep_intel.topology.discovery import discover, load_effective

__all__ = ["discover", "load_effective"]
