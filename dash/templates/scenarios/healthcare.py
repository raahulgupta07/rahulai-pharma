"""Bundled healthcare / hospital ops scenarios for AutoSim W1."""
from __future__ import annotations

SCENARIOS: list[dict] = [
    {
        "name": "ED capacity surge — flu season weekend",
        "scenario_text": (
            "Emergency department capacity surge: 180% of normal Friday-Sunday during "
            "flu season. Model triage wait times, bed-block from inpatient flow, "
            "staff overtime, and diversion-decision threshold across 5 days."
        ),
        "horizon_days": 5,
        "actors_max": 40,
        "category": "capacity",
        "difficulty": "hard",
        "expected_findings": [
            "triage wait-time hours",
            "bed-block bottleneck unit",
            "diversion trigger time",
        ],
    },
    {
        "name": "MRI machine outage — 72h scheduled maintenance",
        "scenario_text": (
            "Main MRI machine offline 72 hours for scheduled maintenance. "
            "Model patient-reschedule backlog, outsource referral cost, "
            "revenue deferral, and catch-up scheduling over 14 days."
        ),
        "horizon_days": 14,
        "actors_max": 25,
        "category": "equipment",
        "difficulty": "medium",
        "expected_findings": [
            "reschedule backlog size",
            "outsource referral spend",
            "catch-up wait time",
        ],
    },
    {
        "name": "Nursing staff strike — 48h authorized",
        "scenario_text": (
            "Nursing union authorizes 48-hour strike starting Monday 7am. Model "
            "agency-staff coverage cost, elective-surgery cancellations, patient "
            "safety risk, and 14-day recovery curve."
        ),
        "horizon_days": 14,
        "actors_max": 35,
        "category": "labor",
        "difficulty": "hard",
        "expected_findings": [
            "agency cost premium",
            "elective revenue loss",
            "patient-safety incident risk",
        ],
    },
    {
        "name": "CMS audit — readmission rates flagged",
        "scenario_text": (
            "CMS flags 30-day readmission rates for CHF and pneumonia patients "
            "above benchmark. On-site audit in 21 days. Model documentation "
            "remediation, care-coordination intervention, and penalty-risk exposure."
        ),
        "horizon_days": 30,
        "actors_max": 25,
        "category": "compliance",
        "difficulty": "medium",
        "expected_findings": [
            "documentation gaps",
            "intervention readmission impact",
            "penalty exposure $",
        ],
    },
    {
        "name": "Infection outbreak — 8 ICU patients in 5 days",
        "scenario_text": (
            "Healthcare-acquired infection outbreak: 8 ICU patients positive in "
            "5 days. Model isolation-bed reshuffling, contact-tracing workload, "
            "elective postponements, and 21-day containment curve."
        ),
        "horizon_days": 21,
        "actors_max": 30,
        "category": "infection_control",
        "difficulty": "hard",
        "expected_findings": [
            "isolation capacity gap",
            "containment date",
            "elective revenue impact",
        ],
    },
]
