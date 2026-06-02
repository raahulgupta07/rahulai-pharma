"""Bundled pharmacy scenarios for AutoSim W1."""
from __future__ import annotations

SCENARIOS: list[dict] = [
    {
        "name": "Aspirin stockout cascade",
        "scenario_text": (
            "Yangon central pharmacy runs out of Aspirin 500mg Monday morning. "
            "Demand spike across 20 nearby pharmacies starts Tuesday as walk-ins "
            "are redirected. Regional distributor reports shortage Wednesday. "
            "Model substitution patterns, lost sales, and reorder cascades over 7 days."
        ),
        "horizon_days": 7,
        "actors_max": 30,
        "category": "stockout",
        "difficulty": "medium",
        "expected_findings": [
            "substitution patterns to ibuprofen/paracetamol",
            "lost-sales estimate by store",
            "distributor reorder bottleneck",
        ],
    },
    {
        "name": "Insulin demand spike — heatwave week",
        "scenario_text": (
            "Heatwave drives 35% spike in insulin pen demand across diabetic patients. "
            "Cold-chain capacity at chain warehouses is 80% utilized at baseline. "
            "Model reorder timing, refrigeration capacity, and patient compliance "
            "over 14 days assuming 200 chronic patients per store."
        ),
        "horizon_days": 14,
        "actors_max": 40,
        "category": "demand_spike",
        "difficulty": "hard",
        "expected_findings": [
            "cold-chain saturation date",
            "refill adherence gap",
            "branch-level reorder priority",
        ],
    },
    {
        "name": "New flagship store opens — Mandalay district",
        "scenario_text": (
            "New flagship pharmacy opens in Mandalay with 2x SKU breadth vs nearby "
            "stores. Forecast first-90-day footfall, cannibalization on existing "
            "branches within 3km, SKU mix tuning, and steady-state assortment."
        ),
        "horizon_days": 90,
        "actors_max": 25,
        "category": "expansion",
        "difficulty": "medium",
        "expected_findings": [
            "cannibalization radius",
            "first-month assortment misses",
            "break-even week",
        ],
    },
    {
        "name": "Cold-chain outage 18h — central depot",
        "scenario_text": (
            "Central depot loses refrigeration for 18 hours after generator failure. "
            "120 SKUs (vaccines, insulin, biologics) are at risk. Model quarantine "
            "decisions, replacement-order timing, patient impact, and write-off cost "
            "across 7 days."
        ),
        "horizon_days": 7,
        "actors_max": 20,
        "category": "operational_outage",
        "difficulty": "hard",
        "expected_findings": [
            "salvageable SKU count",
            "patient-rescheduling load",
            "write-off total",
        ],
    },
    {
        "name": "Controlled-substance audit — 3 branches flagged",
        "scenario_text": (
            "Regulator flags 3 branches for controlled-substance reconciliation audit "
            "next week. Stock counts, prescription records, and refill rates must align. "
            "Model staff workload, customer wait-time impact, and remediation timeline "
            "over 10 days."
        ),
        "horizon_days": 10,
        "actors_max": 25,
        "category": "compliance",
        "difficulty": "medium",
        "expected_findings": [
            "documentation gaps",
            "staff overtime cost",
            "customer-impact window",
        ],
    },
    {
        "name": "Flu season ramp — 6-week outlook",
        "scenario_text": (
            "Flu season begins. Historical patterns show 3x demand for cold remedies, "
            "antivirals, and OTC fever reducers across 6 weeks. Model staffing, "
            "reorder cadence, and inter-store transfers across 60 branches."
        ),
        "horizon_days": 42,
        "actors_max": 60,
        "category": "seasonality",
        "difficulty": "medium",
        "expected_findings": [
            "peak-week date",
            "transfer routes optimal vs current",
            "OOS risk SKUs",
        ],
    },
]
