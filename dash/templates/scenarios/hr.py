"""Bundled HR / people scenarios for AutoSim W1."""
from __future__ import annotations

SCENARIOS: list[dict] = [
    {
        "name": "Comp adjustment +6% — engineering band",
        "scenario_text": (
            "Engineering band gets 6% comp adjustment next quarter. Model attrition "
            "drop, hiring-funnel improvement, internal equity ripple to adjacent "
            "bands, and budget impact over 180 days."
        ),
        "horizon_days": 180,
        "actors_max": 30,
        "category": "compensation",
        "difficulty": "medium",
        "expected_findings": [
            "attrition delta",
            "internal-equity complaints",
            "offer-accept lift",
        ],
    },
    {
        "name": "Mass resignation — 12 senior leaves in one week",
        "scenario_text": (
            "12 senior ICs (5+ year tenure) resign in one week, all citing the "
            "same VP. Model knowledge-transfer risk, backfill timeline, project "
            "slippage, and morale contagion across 90 days."
        ),
        "horizon_days": 90,
        "actors_max": 25,
        "category": "retention",
        "difficulty": "hard",
        "expected_findings": [
            "contagion radius",
            "backfill lead-time",
            "project-slip impact",
        ],
    },
    {
        "name": "New department head — re-org first 90 days",
        "scenario_text": (
            "External hire takes over a 60-person department. Model engagement "
            "score trajectory, voluntary attrition, productivity dip, and "
            "team-restructure decisions over first 90 days."
        ),
        "horizon_days": 90,
        "actors_max": 35,
        "category": "leadership_change",
        "difficulty": "medium",
        "expected_findings": [
            "engagement dip and recovery",
            "regrettable attrition",
            "decision-velocity change",
        ],
    },
    {
        "name": "Hiring freeze announced — 6 months",
        "scenario_text": (
            "Company-wide hiring freeze announced for next 6 months except critical "
            "roles. Model open-req aging, internal-mobility uptick, voluntary "
            "attrition response, and team workload across affected functions."
        ),
        "horizon_days": 180,
        "actors_max": 30,
        "category": "workforce_plan",
        "difficulty": "medium",
        "expected_findings": [
            "critical-role bottlenecks",
            "internal mobility lift",
            "burnout signal",
        ],
    },
    {
        "name": "Annual bonus delayed 60 days",
        "scenario_text": (
            "Annual bonus payout pushed 60 days due to delayed audit. Model "
            "morale impact, attrition spike risk in top-quartile performers, "
            "and recovery-communication strategy over 90 days."
        ),
        "horizon_days": 90,
        "actors_max": 30,
        "category": "compensation",
        "difficulty": "medium",
        "expected_findings": [
            "top-talent attrition risk",
            "engagement-survey dip",
            "comms-strategy ROI",
        ],
    },
    {
        "name": "Office relocation — 8km move, mandatory in-office days",
        "scenario_text": (
            "Office relocates 8km away and adds 2 mandatory in-office days. Model "
            "voluntary attrition by tenure / role / commute distance, productivity "
            "impact, and accommodation requests over 120 days."
        ),
        "horizon_days": 120,
        "actors_max": 40,
        "category": "workplace_change",
        "difficulty": "hard",
        "expected_findings": [
            "attrition by commute band",
            "accommodation backlog",
            "productivity recovery curve",
        ],
    },
]
