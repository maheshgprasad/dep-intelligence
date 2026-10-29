"""Point-in-time repository and cluster intelligence reports.

These reports read a published snapshot. They do not refresh graphs, rank
impact, or run Boundary Gate.
"""

from dep_intel.reporting.builder import generate_report

__all__ = ["generate_report"]
