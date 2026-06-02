"""AI deck stylist — inspects a dashboard spec, returns theme + palette
overrides + narrative tone for the dashboard→deck pipeline.

Stage 1: deterministic profile extraction (free, ~1ms).
Stage 2: LITE_MODEL agent (≤1.5s, ~$0.0008) picks theme + accent.
Fail-soft: domain-keyword fallback when LLM unavailable.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


REGISTERED_THEMES = [
    "coral_energy", "midnight_executive", "forest_moss",
    "ocean_gradient", "charcoal_minimal", "teal_trust",
    "berry_cream", "cherry_bold",
]


# ── Contrast helpers (WCAG luminance) ────────────────────────────────────
def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    s = (h or "").lstrip("#")
    if len(s) != 6:
        return (0, 0, 0)
    try:
        return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
    except Exception:
        return (0, 0, 0)


def _luminance(hex_color: str) -> float:
    """Relative luminance per WCAG 2.0."""
    r, g, b = _hex_to_rgb(hex_color)
    def chan(c: int) -> float:
        x = c / 255.0
        return x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4
    return 0.2126 * chan(r) + 0.7152 * chan(g) + 0.0722 * chan(b)


def _contrast_ratio(c1: str, c2: str) -> float:
    l1 = _luminance(c1)
    l2 = _luminance(c2)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


def pick_readable_text(bg_hex: str, light: str = "FFFFFF", dark: str = "1A1614") -> str:
    """Auto-pick light or dark text for given background. Always WCAG-safe."""
    if not bg_hex:
        return dark
    return light if _luminance(bg_hex) < 0.45 else dark


def ensure_contrast(fg_hex: str, bg_hex: str, min_ratio: float = 4.5) -> str:
    """If fg/bg contrast below ratio, swap fg to white-or-dark whichever wins."""
    if not fg_hex or not bg_hex:
        return fg_hex
    if _contrast_ratio(fg_hex, bg_hex) >= min_ratio:
        return fg_hex
    return pick_readable_text(bg_hex)

# Domain keyword → preferred theme (deterministic fallback)
_DOMAIN_THEMES: dict[str, list[str]] = {
    "coral_energy":      ["campaign", "marketing", "retail", "ecommerce", "promo", "crm", "engagement"],
    "teal_trust":        ["finance", "fp&a", "treasury", "banking", "loan", "credit", "accounting", "investment"],
    "forest_moss":       ["healthcare", "clinical", "pharma", "pharmacy", "patient", "hospital", "wellness"],
    "cherry_bold":       ["risk", "alert", "compliance", "fraud", "incident", "violation", "breach", "critical"],
    "midnight_executive": ["executive", "board", "investor", "qbr", "summary", "strategic", "roadmap"],
    "charcoal_minimal":  ["ops", "operational", "supply", "logistics", "warehouse", "throughput", "manufacturing"],
    "berry_cream":       ["consumer", "lifestyle", "brand", "loyalty", "hotel", "hospitality"],
    "ocean_gradient":    ["ocean", "sustainability", "esg", "environment", "water"],
}

_MOOD_LEXICON = {
    "alert":    ["issue", "lack", "fluctuat", "drop", "below", "gap", "risk", "failed",
                 "incorrect", "missing", "decline", "deteriorat", "concern", "violation"],
    "positive": ["growth", "exceed", "drove", "achiev", "lifted", "surpass", "improved",
                 "best", "leading", "win", "uplift", "gain"],
}


def _extract_text_corpus(spec: dict, panels: list[dict]) -> str:
    """Concat all human-readable strings from spec for keyword scan."""
    parts: list[str] = []
    for k in ("title", "intent", "persona", "audience", "narrative"):
        v = spec.get(k)
        if isinstance(v, str):
            parts.append(v)
    for p in panels:
        for k in ("title", "narrative"):
            v = p.get(k)
            if isinstance(v, str):
                parts.append(v)
        for src in (p.get("sources") or []):
            if isinstance(src, str):
                parts.append(src)
    return " ".join(parts).lower()


def _detect_domain(corpus: str) -> tuple[str, str]:
    """Return (theme_name, matched_keyword) using keyword scoring."""
    scores: dict[str, int] = {t: 0 for t in REGISTERED_THEMES}
    matched: dict[str, str] = {}
    for theme, kws in _DOMAIN_THEMES.items():
        for kw in kws:
            if kw in corpus:
                scores[theme] += 1
                matched.setdefault(theme, kw)
    best = max(scores.items(), key=lambda kv: kv[1])
    if best[1] == 0:
        return "coral_energy", "default"
    return best[0], matched.get(best[0], "")


def _detect_mood(corpus: str) -> str:
    a = sum(1 for kw in _MOOD_LEXICON["alert"] if kw in corpus)
    p = sum(1 for kw in _MOOD_LEXICON["positive"] if kw in corpus)
    if a >= max(2, p + 1):
        return "alert"
    if p >= max(2, a + 1):
        return "positive"
    return "neutral"


def _detect_audience(spec: dict, corpus: str) -> str:
    aud = (spec.get("audience") or "").lower().strip()
    if aud:
        return aud
    if any(t in corpus for t in ("board", "investor", "executive", "ceo", "cfo")):
        return "executive"
    if any(t in corpus for t in ("ops", "operational", "team", "frontline")):
        return "operational"
    return "general"


def analyze_dashboard(spec: dict, panels: list[dict]) -> dict:
    """Stage 1 — deterministic profile. $0 / sub-ms."""
    corpus = _extract_text_corpus(spec, panels)
    theme_guess, matched_kw = _detect_domain(corpus)
    mood = _detect_mood(corpus)
    audience = _detect_audience(spec, corpus)

    chart_types = []
    for p in panels:
        ct = (p.get("chart_type") or "").lower()
        if ct:
            chart_types.append(ct)

    profile = {
        "title": (spec.get("title") or "")[:200],
        "audience": audience,
        "persona": (spec.get("persona") or "")[:100],
        "mood": mood,
        "domain_guess": theme_guess,
        "domain_keyword": matched_kw,
        "panel_count": len(panels),
        "chart_types": chart_types,
        "panel_snippets": [
            (p.get("narrative") or p.get("title") or "")[:160]
            for p in panels[:8]
        ],
        "project_slug": spec.get("project_slug") or spec.get("_project_slug", ""),
    }
    return profile


def _llm_pick_style(profile: dict) -> dict | None:
    """Stage 2 — LLM picks theme + accent. Returns None on failure."""
    try:
        from dash.settings import training_llm_call
    except Exception as e:
        logger.debug("training_llm_call import failed: %s", e)
        return None

    prompt = f"""You are a presentation designer. Pick visual style for a slide deck
generated from this dashboard.

DASHBOARD PROFILE:
- Title: {profile['title']}
- Audience: {profile['audience']}
- Persona: {profile['persona']}
- Mood: {profile['mood']}  (alert=urgent issues, positive=growth wins, neutral=facts)
- Domain hint: {profile['domain_guess']}
- Panels: {profile['panel_count']} — chart types: {", ".join(profile['chart_types'][:6])}
- Snippets:
{chr(10).join("  - " + s for s in profile['panel_snippets'][:6])}

AVAILABLE THEMES (pick exactly one):
- coral_energy:      warm cream + coral — retail, marketing, campaigns
- midnight_executive: dark + teal — board, investor, executive
- forest_moss:       muted green — healthcare, pharma, wellness, sustainability
- ocean_gradient:    light blue — ESG, environment, calm topics
- charcoal_minimal:  grayscale — ops, supply chain, precision
- teal_trust:        teal — finance, banking, FP&A, trust
- berry_cream:       pink + cream — consumer, lifestyle, brand
- cherry_bold:       red on white — risk, alert, compliance, urgency

Output ONLY valid JSON in this exact shape (no fences, no preamble):
{{
  "theme_name": "<one of the 8 above>",
  "accent_hex": "#RRGGBB (brand accent color — used for eyebrows, lines, bars)",
  "card_bg_hex": "#RRGGBB (KPI card / tile background — pick contrasting solid)",
  "card_value_hex": "#RRGGBB (big number text on card — MUST contrast card_bg 4.5+)",
  "card_label_hex": "#RRGGBB (small label text on card — MUST contrast card_bg 4.5+)",
  "chart_palette": ["#RRGGBB", "#RRGGBB", "#RRGGBB", "#RRGGBB", "#RRGGBB"],
  "narrative_tone": "executive | analytical | alert",
  "reasoning": "one short sentence why this palette fits the mood + WCAG-safe"
}}

CONTRAST RULES (CRITICAL):
- card_value_hex AND card_label_hex MUST be readable on card_bg_hex (WCAG ≥ 4.5:1)
- DARK card_bg (luminance <0.4) → use WHITE or near-white text (#FFFFFF, #F5F2EC)
- LIGHT card_bg (luminance >0.6) → use DARK text (#1A1614, #2C2A26)
- NEVER red text on dark blue, NEVER yellow on white
- chart_palette: 5 distinct hues that work on a CREAM or WHITE chart background
"""
    try:
        raw = training_llm_call(prompt, task="extraction")
    except Exception as e:
        logger.warning("stylist LLM call failed: %s", e)
        return None
    if not raw:
        return None

    # Strip fences
    txt = raw.strip()
    if txt.startswith("```"):
        txt = re.sub(r"^```(?:json)?\s*", "", txt)
        txt = re.sub(r"\s*```$", "", txt)
    # Find first JSON object
    m = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", txt, re.DOTALL)
    if m:
        txt = m.group(0)
    try:
        parsed = json.loads(txt)
    except Exception as e:
        logger.warning("stylist JSON parse failed: %s", e)
        return None

    theme = (parsed.get("theme_name") or "").strip()
    if theme not in REGISTERED_THEMES:
        logger.info("stylist returned unknown theme %r, falling through", theme)
        return None

    def _norm_hex(v: Any) -> str:
        s = str(v or "").strip().lstrip("#")
        if len(s) == 6 and all(c in "0123456789abcdefABCDEF" for c in s):
            return "#" + s.upper()
        return ""

    accent = _norm_hex(parsed.get("accent_hex"))
    card_bg = _norm_hex(parsed.get("card_bg_hex"))
    card_val = _norm_hex(parsed.get("card_value_hex"))
    card_lbl = _norm_hex(parsed.get("card_label_hex"))

    # Enforce WCAG contrast — auto-fix if LLM picked clashing colors
    if card_bg:
        if card_val:
            card_val = ensure_contrast(card_val.lstrip("#"), card_bg.lstrip("#"), 4.5)
            if not card_val.startswith("#"):
                card_val = "#" + card_val
        else:
            card_val = "#" + pick_readable_text(card_bg.lstrip("#"))
        if card_lbl:
            card_lbl = ensure_contrast(card_lbl.lstrip("#"), card_bg.lstrip("#"), 4.5)
            if not card_lbl.startswith("#"):
                card_lbl = "#" + card_lbl
        else:
            card_lbl = "#" + pick_readable_text(card_bg.lstrip("#"))

    raw_palette = parsed.get("chart_palette") or []
    palette: list[str] = []
    if isinstance(raw_palette, list):
        for c in raw_palette[:7]:
            n = _norm_hex(c)
            if n:
                palette.append(n)

    return {
        "theme_name": theme,
        "accent_hex": accent or "",
        "card_bg_hex": card_bg or "",
        "card_value_hex": card_val or "",
        "card_label_hex": card_lbl or "",
        "chart_palette": palette,
        "narrative_tone": (parsed.get("narrative_tone") or "executive").strip().lower(),
        "reasoning": (parsed.get("reasoning") or "")[:280],
        "source": "llm",
    }


def choose_style(spec: dict, panels: list[dict]) -> dict:
    """Full pipeline: profile → LLM → fallback.

    Returns DeckStyle dict:
        {theme_name, accent_hex, palette_hex, narrative_tone, reasoning,
         source ("llm"|"fallback"), profile}
    Never raises.
    """
    profile = analyze_dashboard(spec, panels)

    style = _llm_pick_style(profile)
    if style is None:
        theme = profile["domain_guess"]
        if profile["mood"] == "alert" and profile["domain_guess"] not in {"cherry_bold", "coral_energy"}:
            theme = "cherry_bold"
        style = {
            "theme_name": theme,
            "accent_hex": "",
            "card_bg_hex": "",
            "card_value_hex": "",
            "card_label_hex": "",
            "chart_palette": [],
            "narrative_tone": "alert" if profile["mood"] == "alert" else "executive",
            "reasoning": (
                f"Fallback (no LLM): domain keyword '{profile['domain_keyword']}' "
                f"→ {theme}; mood {profile['mood']}"
            ),
            "source": "fallback",
        }

    # Backward-compat alias used by dashboard_to_deck
    style["palette_hex"] = style.get("chart_palette") or []
    style["profile"] = profile
    return style
