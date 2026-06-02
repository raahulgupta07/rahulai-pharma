"""Auto-icon matcher for slide bullets.

Maps bullet text keywords → Lucide icon name. Pure Python keyword scoring,
no LLM, no network. Frontend (which already bundles lucide-svelte) renders
the matched icon next to each bullet.

Usage:
    from dash.tools.slide_icons import pick_icon
    icon_name = pick_icon("Revenue grew 23% YoY")
    # → "trending-up"
"""
from __future__ import annotations

import re
from typing import List, Optional

# ── Static keyword → icon map (~70 most common analytical concepts) ────
# Order matters: more specific keywords first.
_ICON_MAP: List[tuple[str, str]] = [
    # Money + finance
    (r"\b(revenue|sales|income|earning|gross)\b", "dollar-sign"),
    (r"\b(profit|margin|ebitda|net)\b", "trending-up"),
    (r"\b(loss|losses|deficit|negative)\b", "trending-down"),
    (r"\b(cost|expense|spend|budget|opex)\b", "wallet"),
    (r"\b(price|pricing|cogs|capex)\b", "tag"),
    (r"\b(roi|return|yield|payback)\b", "percent"),
    (r"\b(cash|cashflow|liquidity)\b", "banknote"),
    (r"\b(forecast|projection|outlook)\b", "telescope"),

    # Growth + trends
    (r"\b(growth|grew|grows|grown|increase|increased|rising|up)\b", "trending-up"),
    (r"\b(decline|declined|drop|dropped|down|decreased|falling)\b", "trending-down"),
    (r"\b(flat|stable|stagnant|unchanged)\b", "minus"),
    (r"\b(trend|trends|pattern|momentum)\b", "line-chart"),
    (r"\b(growth.{0,10}rate|cagr|yoy|mom|qoq)\b", "trending-up"),

    # Customers + users
    (r"\b(customer|customers|client|clients|buyer|buyers)\b", "users"),
    (r"\b(user|users|account|accounts)\b", "user"),
    (r"\b(retention|retain|stay|loyal)\b", "heart"),
    (r"\b(churn|attrition|lost|leaving)\b", "user-minus"),
    (r"\b(acquired|acquisition|new customer)\b", "user-plus"),
    (r"\b(satisfaction|nps|csat|happiness)\b", "smile"),
    (r"\b(segment|segmentation|cohort)\b", "pie-chart"),

    # Product + inventory
    (r"\b(product|sku|item|catalog)\b", "package"),
    (r"\b(inventory|stock|warehouse)\b", "box"),
    (r"\b(supply|supplier|vendor)\b", "truck"),
    (r"\b(demand|order|orders)\b", "shopping-cart"),
    (r"\b(launch|rollout|release)\b", "rocket"),

    # Performance + quality
    (r"\b(performance|kpi|metric|metrics)\b", "gauge"),
    (r"\b(quality|defect|fail|failure)\b", "alert-triangle"),
    (r"\b(success|achieved|hit)\b", "check-circle"),
    (r"\b(risk|warning|caution)\b", "alert-octagon"),
    (r"\b(target|goal|objective)\b", "target"),
    (r"\b(benchmark|baseline|industry avg)\b", "ruler"),

    # Time + speed
    (r"\b(time|hour|hours|day|days|week|month|year)\b", "clock"),
    (r"\b(fast|speed|quick|rapid)\b", "zap"),
    (r"\b(slow|lag|delay)\b", "snail"),
    (r"\b(deadline|due|sched)\b", "calendar"),

    # Geography + scale
    (r"\b(region|country|global|world|international)\b", "globe"),
    (r"\b(location|store|branch|office)\b", "map-pin"),
    (r"\b(market|share|penetration)\b", "pie-chart"),
    (r"\b(local|domestic|home)\b", "home"),

    # Comparisons + analysis
    (r"\b(compare|comparison|vs|versus|against)\b", "git-compare"),
    (r"\b(analysis|analyze|investigate)\b", "search"),
    (r"\b(diagnose|root cause|driver)\b", "stethoscope"),
    (r"\b(top|best|leader|first)\b", "trophy"),
    (r"\b(bottom|worst|laggard|last)\b", "arrow-down"),
    (r"\b(anomaly|outlier|spike)\b", "activity"),

    # Action + recommendation
    (r"\b(recommend|recommendation|action|next step)\b", "arrow-right"),
    (r"\b(launch|build|create|develop)\b", "hammer"),
    (r"\b(fix|repair|resolve|patch)\b", "wrench"),
    (r"\b(invest|investment|capital)\b", "coins"),
    (r"\b(approve|approval|sign)\b", "check"),
    (r"\b(reject|block|deny)\b", "x"),

    # Data + tech
    (r"\b(data|database|table|row|record)\b", "database"),
    (r"\b(query|sql|select)\b", "terminal"),
    (r"\b(model|ml|ai|algorithm)\b", "cpu"),
    (r"\b(pipeline|workflow|process)\b", "git-branch"),
    (r"\b(api|integration|connect)\b", "plug"),

    # People + org
    (r"\b(team|staff|employee|headcount)\b", "users"),
    (r"\b(leader|leadership|exec|ceo|cfo|cto)\b", "crown"),
    (r"\b(department|division|unit)\b", "building-2"),

    # Healthcare / pharma specific (since user has pharma agent)
    (r"\b(prescription|rx|medication|drug)\b", "pill"),
    (r"\b(dose|dispensing|fill)\b", "syringe"),
    (r"\b(patient|clinical|diagnosis)\b", "heart-pulse"),
    (r"\b(compliance|regulation|fda|dea)\b", "shield-check"),
    (r"\b(audit|inspection|review)\b", "clipboard-check"),

    # Retail specific
    (r"\b(basket|cart|checkout)\b", "shopping-bag"),
    (r"\b(discount|promo|sale)\b", "percent"),
    (r"\b(brand|category|line)\b", "layers"),

    # Generic fallback markers
    (r"^\s*\d", "hash"),     # bullet starting w/ number
    (r"\?$", "help-circle"), # ends w/ question mark
]

_DEFAULT_ICON = "circle"


def pick_icon(text: Optional[str]) -> str:
    """Match bullet text to a Lucide icon name. Returns default 'circle' on miss."""
    if not text:
        return _DEFAULT_ICON
    t = text.lower().strip()
    if not t:
        return _DEFAULT_ICON
    for pat, icon in _ICON_MAP:
        if re.search(pat, t):
            return icon
    return _DEFAULT_ICON


def pick_icons(bullets: List[str]) -> List[str]:
    """Batch — returns list of icon names matching each bullet."""
    return [pick_icon(b) for b in (bullets or [])]


def enrich_slide_with_icons(slide_spec: dict) -> dict:
    """Mutate slide spec in place, add `bullet_icons` field. Returns the spec."""
    bullets = slide_spec.get("bullets") or []
    slide_spec["bullet_icons"] = pick_icons(bullets)
    return slide_spec
