"""Template infrastructure package.

NOTE: Industry preset vertical templates have been removed (formerly bank.py,
retail.py, pharmacy_network.py, etc., plus registry.py and apply.py). The
remaining modules — `schema`, `storage`, `reconcile`, `customer_scores`,
`runner`, `scenarios`, `seeds` — are shared infrastructure still used by
customer_360, sim_api, ontology_api, upload, instructions, tools, and the
autonomous workflow runner.
"""
from __future__ import annotations

from .schema import AgentTemplate, ExpectedEntity, ExpectedColumn, KPISpec, AutonomousWorkflow

__all__ = [
    "AgentTemplate",
    "ExpectedEntity",
    "ExpectedColumn",
    "KPISpec",
    "AutonomousWorkflow",
]
