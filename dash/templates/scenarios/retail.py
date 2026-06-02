"""Bundled retail scenarios for AutoSim W1."""
from __future__ import annotations

SCENARIOS: list[dict] = [
    {
        "name": "Bestseller price hike +8%",
        "scenario_text": (
            "Marketing pushes 8% price increase on top-20 bestsellers next Monday. "
            "Model unit demand elasticity, basket-mix shift, and total revenue over "
            "21 days across 50 stores. Account for loyalty-tier discount layering."
        ),
        "horizon_days": 21,
        "actors_max": 50,
        "category": "pricing",
        "difficulty": "medium",
        "expected_findings": [
            "elasticity by category",
            "basket cannibalization",
            "loyalty-tier reaction",
        ],
    },
    {
        "name": "Competitor flash discount 25% — adjacent mall",
        "scenario_text": (
            "Major competitor opens 25% storewide discount in same mall for 10 days. "
            "Model footfall loss, defection by customer segment, and matching-response "
            "scenarios (no response vs 10% match vs 20% match)."
        ),
        "horizon_days": 10,
        "actors_max": 40,
        "category": "competitive",
        "difficulty": "hard",
        "expected_findings": [
            "footfall drop %",
            "RFM segment defection",
            "margin tradeoff at each response level",
        ],
    },
    {
        "name": "Key supplier outage — apparel category",
        "scenario_text": (
            "Tier-1 apparel supplier goes offline for 14 days. 35% of seasonal SKUs "
            "blocked. Model replacement sourcing lead-time, lost-sales, and "
            "substitute-SKU velocity across 30 stores."
        ),
        "horizon_days": 14,
        "actors_max": 30,
        "category": "supply",
        "difficulty": "medium",
        "expected_findings": [
            "lost-sales by SKU",
            "substitute conversion rate",
            "replenishment date",
        ],
    },
    {
        "name": "OOS cascade — top-10 SKUs go stockout sequentially",
        "scenario_text": (
            "Top-10 SKUs run out of stock over 5 consecutive days due to "
            "underforecast. Model basket downgrade, abandonment, and replenishment "
            "race across 25 high-traffic stores."
        ),
        "horizon_days": 7,
        "actors_max": 25,
        "category": "stockout",
        "difficulty": "medium",
        "expected_findings": [
            "abandonment rate",
            "downgrade-SKU revenue",
            "next-week recovery curve",
        ],
    },
    {
        "name": "Loyalty program launch — points + tier badges",
        "scenario_text": (
            "New 3-tier loyalty program launches Monday with sign-up bonus and 2x "
            "points week-one. Model adoption rate, first-purchase lift, and "
            "tier-progression behavior over 30 days across 40 stores."
        ),
        "horizon_days": 30,
        "actors_max": 40,
        "category": "loyalty",
        "difficulty": "medium",
        "expected_findings": [
            "adoption rate by segment",
            "first-purchase basket lift",
            "tier-up timing",
        ],
    },
    {
        "name": "Black Friday surge — 4-day peak",
        "scenario_text": (
            "Black Friday weekend: 4x normal traffic projected Thu-Sun. Model queue "
            "buildup, OOS risk on doorbuster SKUs, staff scheduling, and online vs "
            "in-store split across 60 stores."
        ),
        "horizon_days": 4,
        "actors_max": 60,
        "category": "peak_event",
        "difficulty": "hard",
        "expected_findings": [
            "queue wait-time per store",
            "doorbuster OOS hour",
            "online overflow rate",
        ],
    },
]
