"""Bundled hotel / hospitality scenarios for AutoSim W1."""
from __future__ import annotations

SCENARIOS: list[dict] = [
    {
        "name": "Last-minute 30% discount — midweek occupancy push",
        "scenario_text": (
            "Property runs 30% off rate plan T-3 days for Tue/Wed nights to lift "
            "occupancy from 58% to target 80%. Model booking pickup curve, ADR "
            "dilution, repeat-guest cannibalization, and RevPAR across 14 days."
        ),
        "horizon_days": 14,
        "actors_max": 35,
        "category": "pricing",
        "difficulty": "medium",
        "expected_findings": [
            "ADR-vs-occupancy tradeoff",
            "ancillary revenue lift",
            "loyal-guest dilution",
        ],
    },
    {
        "name": "OTA channel outage — Booking.com 24h",
        "scenario_text": (
            "Booking.com goes down for 24 hours during high-season Friday. "
            "Model channel-shift to Expedia / direct / phone, cancellation rate, "
            "and revenue recovery over 7 days for 200-room property."
        ),
        "horizon_days": 7,
        "actors_max": 30,
        "category": "channel",
        "difficulty": "medium",
        "expected_findings": [
            "channel-shift % to direct",
            "cancellation spike",
            "rate parity issues",
        ],
    },
    {
        "name": "Corporate group cancellation — 60 rooms / 3 nights",
        "scenario_text": (
            "Major corporate group cancels 60-room block T-7 days. Model "
            "remarketing path (FIT vs leisure groups vs walk-in discount), "
            "F&B impact, and final RevPAR outcome over 10 days."
        ),
        "horizon_days": 10,
        "actors_max": 25,
        "category": "group",
        "difficulty": "medium",
        "expected_findings": [
            "best remarketing channel",
            "F&B cover loss",
            "walk-in pricing band",
        ],
    },
    {
        "name": "Competitor opens new property 500m away",
        "scenario_text": (
            "New 4-star property opens 500m away with introductory rates 20% below "
            "yours. Model market-share erosion, segment defection (corporate vs "
            "leisure), and rate-response options across 60 days."
        ),
        "horizon_days": 60,
        "actors_max": 40,
        "category": "competitive",
        "difficulty": "hard",
        "expected_findings": [
            "share loss by segment",
            "rate match break-even",
            "F&B / spa offset value",
        ],
    },
    {
        "name": "Holiday weekend +180% demand",
        "scenario_text": (
            "Public holiday weekend triples normal demand. Model dynamic-pricing "
            "ceiling, overbooking strategy, walk-in rate, and staff coverage across "
            "5 days for 200-room property."
        ),
        "horizon_days": 5,
        "actors_max": 30,
        "category": "peak_event",
        "difficulty": "hard",
        "expected_findings": [
            "optimal price ceiling",
            "overbook-relocation cost",
            "service-quality risk",
        ],
    },
    {
        "name": "Viral 1-star review — TripAdvisor front page",
        "scenario_text": (
            "Viral negative review hits TripAdvisor front page Tuesday. Model "
            "bookings drop curve, ADR pressure, response-recovery actions (PR / "
            "offers / influencer outreach), and 60-day reputation rebuild."
        ),
        "horizon_days": 60,
        "actors_max": 30,
        "category": "reputation",
        "difficulty": "hard",
        "expected_findings": [
            "booking drop %",
            "recovery campaign ROI",
            "review-velocity needed",
        ],
    },
]
