"""Phase-6 layout templates — adaptive grids per persona/question."""
from __future__ import annotations

TEMPLATES = {
    "executive": {
        "description": "4 KPIs top + 1 trend + 1 mix + table bottom. For C-level weekly review.",
        "grid": [
            {"role": "kpi",   "grid": [0, 0, 3, 2]},
            {"role": "kpi",   "grid": [3, 0, 3, 2]},
            {"role": "kpi",   "grid": [6, 0, 3, 2]},
            {"role": "kpi",   "grid": [9, 0, 3, 2]},
            {"role": "trend", "grid": [0, 2, 8, 4]},
            {"role": "mix",   "grid": [8, 2, 4, 4]},
            {"role": "table", "grid": [0, 6, 12, 3]},
        ],
    },
    "operational": {
        "description": "Live status. Many KPIs, alert feed.",
        "grid": [
            {"role": "kpi",    "grid": [0, 0, 2, 2]},
            {"role": "kpi",    "grid": [2, 0, 2, 2]},
            {"role": "kpi",    "grid": [4, 0, 2, 2]},
            {"role": "kpi",    "grid": [6, 0, 2, 2]},
            {"role": "kpi",    "grid": [8, 0, 2, 2]},
            {"role": "kpi",    "grid": [10, 0, 2, 2]},
            {"role": "trend",  "grid": [0, 2, 8, 3]},
            {"role": "alerts", "grid": [8, 2, 4, 5]},
            {"role": "table",  "grid": [0, 5, 8, 3]},
        ],
    },
    "analytical": {
        "description": "Deep dive. Filters left, chart top, table bottom.",
        "grid": [
            {"role": "filters", "grid": [0, 0, 3, 8]},
            {"role": "kpi",     "grid": [3, 0, 3, 2]},
            {"role": "kpi",     "grid": [6, 0, 3, 2]},
            {"role": "kpi",     "grid": [9, 0, 3, 2]},
            {"role": "trend",   "grid": [3, 2, 9, 4]},
            {"role": "table",   "grid": [3, 6, 9, 3]},
        ],
    },
    "exploratory": {
        "description": "Single insight focus. One big chart + 2 supporting KPIs.",
        "grid": [
            {"role": "kpi",   "grid": [0, 0, 6, 2]},
            {"role": "kpi",   "grid": [6, 0, 6, 2]},
            {"role": "trend", "grid": [0, 2, 12, 6]},
        ],
    },
}


def pick_template(question: str, persona: str, num_insights: int) -> str:
    q = (question or "").lower()
    if "live" in q or "now" in q or "today" in q:
        return "operational"
    if "why" in q or "drive" in q or "explain" in q:
        return "analytical"
    if num_insights == 1 or "focus" in q:
        return "exploratory"
    if persona and any(t in persona.lower() for t in ["cfo", "ceo", "exec", "leader"]):
        return "executive"
    return "executive"
