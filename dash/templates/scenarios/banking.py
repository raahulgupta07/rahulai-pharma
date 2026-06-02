"""Bundled retail-banking scenarios for AutoSim W1."""
from __future__ import annotations

SCENARIOS: list[dict] = [
    {
        "name": "Deposit-rate change — savings APY +75bps",
        "scenario_text": (
            "Marketing increases savings APY by 75bps to defend deposit base. "
            "Model deposit inflow from competitors, internal cannibalization from "
            "checking, NIM compression, and 90-day balance trajectory across "
            "250K accounts."
        ),
        "horizon_days": 90,
        "actors_max": 30,
        "category": "pricing",
        "difficulty": "hard",
        "expected_findings": [
            "competitor-deposit inflow",
            "internal cannibalization %",
            "NIM compression bps",
        ],
    },
    {
        "name": "Card-fraud wave — synthetic-ID attack",
        "scenario_text": (
            "Synthetic-ID attack pattern detected: 2,400 fraud applications in 72 hours. "
            "Model rule-tightening false-positive cost, true-positive savings, "
            "and back-book sweep of similar approved accounts."
        ),
        "horizon_days": 14,
        "actors_max": 25,
        "category": "fraud",
        "difficulty": "hard",
        "expected_findings": [
            "rule-tightening optimum",
            "back-book exposure",
            "FP-customer-friction cost",
        ],
    },
    {
        "name": "Stress test — adverse macro shock published",
        "scenario_text": (
            "Regulator publishes adverse macro stress scenario (GDP -5%, unemployment "
            "+4pp). Model loan-loss reserves, CET1 ratio trajectory, dividend-capacity "
            "decision, and capital-action plan over 90 days."
        ),
        "horizon_days": 90,
        "actors_max": 25,
        "category": "regulatory",
        "difficulty": "hard",
        "expected_findings": [
            "CET1 ratio bottom",
            "ECL provision spike",
            "dividend-cut likelihood",
        ],
    },
    {
        "name": "Branch closure — 3 underperforming branches",
        "scenario_text": (
            "3 underperforming branches close in 60 days. Model customer-attrition "
            "rate by tenure / balance / proximity to next branch, deposit migration "
            "to digital, and 12-month NPV impact."
        ),
        "horizon_days": 90,
        "actors_max": 30,
        "category": "footprint",
        "difficulty": "medium",
        "expected_findings": [
            "attrition by balance tier",
            "digital-shift rate",
            "12mo NPV",
        ],
    },
    {
        "name": "Consumer default surge — auto loans 30+ dpd",
        "scenario_text": (
            "Auto loan portfolio 30+ DPD rises 2.4pp in 30 days, concentrated in "
            "2 metro markets. Model collections-capacity gap, charge-off trajectory, "
            "and underwriting-tightening response over 90 days for 80K accounts."
        ),
        "horizon_days": 90,
        "actors_max": 30,
        "category": "credit_risk",
        "difficulty": "hard",
        "expected_findings": [
            "charge-off forecast",
            "collections-capacity gap",
            "tightening tradeoff",
        ],
    },
]
