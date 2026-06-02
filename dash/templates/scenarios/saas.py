"""Bundled SaaS scenarios for AutoSim W1."""
from __future__ import annotations

SCENARIOS: list[dict] = [
    {
        "name": "Price hike +10% on Pro plan",
        "scenario_text": (
            "Pro plan price increases 10% next month (grandfathered for existing "
            "annual). Model churn delta, downgrade-to-Starter rate, expansion-MRR "
            "drag, and net revenue retention over 90 days for ~5,000 paying accounts."
        ),
        "horizon_days": 90,
        "actors_max": 50,
        "category": "pricing",
        "difficulty": "hard",
        "expected_findings": [
            "monthly vs annual churn split",
            "downgrade-tier flow",
            "NRR delta",
        ],
    },
    {
        "name": "Free tier removed — forced upgrade decision",
        "scenario_text": (
            "Free tier discontinued. Existing free users given 30-day window to "
            "upgrade to Starter or export data. Model conversion rate, support-ticket "
            "spike, brand-sentiment risk, and incremental ARR over 60 days."
        ),
        "horizon_days": 60,
        "actors_max": 40,
        "category": "monetization",
        "difficulty": "hard",
        "expected_findings": [
            "free-to-paid conversion rate",
            "ticket-load spike",
            "incremental ARR vs goodwill cost",
        ],
    },
    {
        "name": "Competitor launches at half the price",
        "scenario_text": (
            "Major competitor launches feature-equivalent product at 50% of our "
            "list price next Monday. Model deal-cycle elongation, win-rate drop, "
            "discount-pressure on renewals, and ICP-segment defense priorities "
            "over 90 days."
        ),
        "horizon_days": 90,
        "actors_max": 40,
        "category": "competitive",
        "difficulty": "hard",
        "expected_findings": [
            "win-rate drop by segment",
            "renewal-discount avg",
            "defensible ICP wedge",
        ],
    },
    {
        "name": "Production outage 4 hours — primary region",
        "scenario_text": (
            "Primary region outage 4 hours during business hours. SLA credit "
            "obligations, ticket-spike timing, churn-risk on top-100 accounts, "
            "and trust-rebuild messaging over 30 days."
        ),
        "horizon_days": 30,
        "actors_max": 30,
        "category": "incident",
        "difficulty": "medium",
        "expected_findings": [
            "SLA-credit total",
            "at-risk renewal MRR",
            "comms-strategy ROI",
        ],
    },
    {
        "name": "Feature deprecation — legacy API sunset in 90 days",
        "scenario_text": (
            "Legacy v1 API deprecated with 90-day sunset window. 1,200 accounts "
            "have integration dependencies. Model migration completion rate, "
            "support escalations, and churn risk on non-migrators."
        ),
        "horizon_days": 90,
        "actors_max": 40,
        "category": "lifecycle",
        "difficulty": "medium",
        "expected_findings": [
            "migration completion %",
            "support-ticket peak",
            "at-risk churn MRR",
        ],
    },
    {
        "name": "Churn investigation — month-3 cohort spike",
        "scenario_text": (
            "Month-3 cohort churn jumped from 4% to 9%. Model root-cause search "
            "via persona segmentation (onboarding completion, feature adoption, "
            "support-touch count), and design intervention experiments over 60 days."
        ),
        "horizon_days": 60,
        "actors_max": 40,
        "category": "churn",
        "difficulty": "hard",
        "expected_findings": [
            "top churn driver",
            "intervention lift estimate",
            "save-team capacity gap",
        ],
    },
]
