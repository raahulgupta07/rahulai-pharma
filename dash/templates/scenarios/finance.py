"""Bundled finance / FP&A scenarios for AutoSim W1."""
from __future__ import annotations

SCENARIOS: list[dict] = [
    {
        "name": "Policy rate +50bps — central bank surprise hike",
        "scenario_text": (
            "Central bank surprise hike of 50bps Friday. Model impact on net "
            "interest margin, deposit beta, loan demand, and prepayment risk on "
            "mortgage book across 90 days."
        ),
        "horizon_days": 90,
        "actors_max": 25,
        "category": "rate_shock",
        "difficulty": "hard",
        "expected_findings": [
            "deposit-beta realized",
            "NIM trajectory",
            "prepayment surge SKUs",
        ],
    },
    {
        "name": "Credit tightening — underwriting tier shift",
        "scenario_text": (
            "Risk committee tightens underwriting one tier (700+ FICO only). "
            "Model approval-rate drop, application volume, and revenue impact "
            "across 60 days, including back-book delinquency improvement."
        ),
        "horizon_days": 60,
        "actors_max": 20,
        "category": "credit_policy",
        "difficulty": "medium",
        "expected_findings": [
            "approval-rate drop",
            "competitor share gain",
            "back-book delinquency curve",
        ],
    },
    {
        "name": "Card-not-present fraud surge — 3x in 48h",
        "scenario_text": (
            "Card-not-present fraud attempts triple over 48 hours, concentrated on "
            "merchant category 5732 (electronics). Model false-positive cost, "
            "true-positive savings, and customer-friction response over 7 days."
        ),
        "horizon_days": 7,
        "actors_max": 25,
        "category": "fraud",
        "difficulty": "hard",
        "expected_findings": [
            "rule-threshold optimum",
            "decline-rate impact",
            "merchant chargeback exposure",
        ],
    },
    {
        "name": "Regulator on-site audit — 14 day window",
        "scenario_text": (
            "Regulator announces on-site audit in 14 days covering AML controls, "
            "KYC refresh rate, and SAR filing timeliness. Model staff workload, "
            "remediation backlog, and findings-risk exposure."
        ),
        "horizon_days": 14,
        "actors_max": 20,
        "category": "compliance",
        "difficulty": "medium",
        "expected_findings": [
            "documentation gaps",
            "overtime cost",
            "MRA likelihood",
        ],
    },
    {
        "name": "SME default wave — 2 sectors flagged",
        "scenario_text": (
            "Two sectors (hospitality + construction) show early-stage delinquency "
            "rising 18% above baseline. Model 12-month default rate, ECL provision "
            "increment, and collections capacity for 30K SME loans."
        ),
        "horizon_days": 90,
        "actors_max": 30,
        "category": "credit_risk",
        "difficulty": "hard",
        "expected_findings": [
            "default-rate forecast",
            "ECL stage transitions",
            "collections capacity gap",
        ],
    },
]
