"""Bundled distribution / 3PL scenarios for AutoSim W1."""
from __future__ import annotations

SCENARIOS: list[dict] = [
    {
        "name": "OTIF drop — 92% → 84% in 14 days",
        "scenario_text": (
            "On-Time-In-Full drops from 92% to 84% over 14 days. Model root-cause "
            "split across pick-rate, dock-dwell, carrier on-time, and stockout. "
            "Run intervention scenarios (extra pickers, carrier swap, safety stock) "
            "over 21 days for 12 DCs."
        ),
        "horizon_days": 21,
        "actors_max": 30,
        "category": "service_level",
        "difficulty": "hard",
        "expected_findings": [
            "primary OTIF driver",
            "intervention ROI ranked",
            "carrier mix optimum",
        ],
    },
    {
        "name": "Supplier outage — top-3 vendor 10 days down",
        "scenario_text": (
            "Top-3 vendor (15% of inbound volume) goes offline 10 days for plant "
            "issue. Model substitution availability, lead-time inflation, "
            "expedite premium, and customer fill-rate degradation over 21 days."
        ),
        "horizon_days": 21,
        "actors_max": 30,
        "category": "supply",
        "difficulty": "hard",
        "expected_findings": [
            "substitution coverage %",
            "expedite premium",
            "fill-rate floor",
        ],
    },
    {
        "name": "Route disruption — bridge closure 7 days",
        "scenario_text": (
            "Major bridge closes 7 days, blocks fastest route to 4 metro stores. "
            "Model rerouting time, fuel-cost increase, driver-overtime, and "
            "delivery-window slips across 14 days."
        ),
        "horizon_days": 14,
        "actors_max": 25,
        "category": "logistics",
        "difficulty": "medium",
        "expected_findings": [
            "rerouting time avg",
            "fuel premium",
            "delivery-window slip %",
        ],
    },
    {
        "name": "Demand spike — promo flips DCs from balanced to surge",
        "scenario_text": (
            "Customer promotion drives 3x order volume into 2 DCs for 7 days. "
            "Model staffing surge, dock capacity, picking-throughput ceiling, "
            "and order-cycle-time impact across 14 days."
        ),
        "horizon_days": 14,
        "actors_max": 30,
        "category": "demand_event",
        "difficulty": "medium",
        "expected_findings": [
            "throughput ceiling",
            "staff overtime",
            "cycle-time slip",
        ],
    },
    {
        "name": "Regulatory change — drive-hours cap reduced",
        "scenario_text": (
            "Regulator reduces daily driver-hours cap by 1 hour. Model network-wide "
            "delivery capacity impact, lane re-routing, additional driver-hire "
            "need, and cost-per-mile change over 60 days."
        ),
        "horizon_days": 60,
        "actors_max": 35,
        "category": "regulatory",
        "difficulty": "hard",
        "expected_findings": [
            "capacity loss %",
            "driver-hire incremental",
            "cost-per-mile delta",
        ],
    },
]
