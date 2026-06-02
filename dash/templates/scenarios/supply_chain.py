"""Bundled supply-chain scenarios for AutoSim W1."""
from __future__ import annotations

SCENARIOS: list[dict] = [
    {
        "name": "Tier-1 supplier outage — 14 day downtime",
        "scenario_text": (
            "Tier-1 supplier of key component goes offline for 14 days due to "
            "factory fire. 28 SKUs depend on this component. Model alternative-source "
            "lead time, expediting cost, and customer-impact across 30 days."
        ),
        "horizon_days": 30,
        "actors_max": 35,
        "category": "supplier",
        "difficulty": "hard",
        "expected_findings": [
            "alt-source lead time",
            "expedite premium cost",
            "OTIF degradation",
        ],
    },
    {
        "name": "Port closure — Yangon 5 days",
        "scenario_text": (
            "Yangon port closes 5 days due to labor dispute. 14 inbound containers "
            "stuck. Model rerouting via Singapore transhipment, demurrage cost, "
            "production-line impact, and customer commit-date slips over 21 days."
        ),
        "horizon_days": 21,
        "actors_max": 30,
        "category": "logistics",
        "difficulty": "hard",
        "expected_findings": [
            "rerouting cost",
            "production-stoppage hours",
            "customer-commit slip %",
        ],
    },
    {
        "name": "Demand spike +40% — single-SKU promo",
        "scenario_text": (
            "Marketing runs 40% off promo on hero SKU for 7 days, expected to "
            "lift demand 4x. Model upstream supplier capacity, transport lane "
            "bottlenecks, and post-promo demand-drop normalization over 21 days."
        ),
        "horizon_days": 21,
        "actors_max": 30,
        "category": "demand_event",
        "difficulty": "medium",
        "expected_findings": [
            "upstream capacity ceiling",
            "lane bottleneck location",
            "post-promo dip depth",
        ],
    },
    {
        "name": "BOM change — substitute component qualifies",
        "scenario_text": (
            "Substitute component qualifies for use across 12 SKUs at 8% lower cost "
            "and 5-day shorter lead time. Model dual-source phase-in, qualification "
            "audit timing, inventory write-down on old stock over 60 days."
        ),
        "horizon_days": 60,
        "actors_max": 25,
        "category": "engineering",
        "difficulty": "medium",
        "expected_findings": [
            "phase-in schedule",
            "obsolescence write-down",
            "qualification audit blockers",
        ],
    },
    {
        "name": "Quality recall — 1 lot, 4,200 units",
        "scenario_text": (
            "Quality issue forces recall of 4,200 units from one lot. Model "
            "customer-notification path, return logistics cost, replacement "
            "inventory pull, and brand-trust recovery actions across 45 days."
        ),
        "horizon_days": 45,
        "actors_max": 30,
        "category": "quality",
        "difficulty": "hard",
        "expected_findings": [
            "return-rate vs notified",
            "replacement-inventory gap",
            "trust-rebuild campaign cost",
        ],
    },
]
