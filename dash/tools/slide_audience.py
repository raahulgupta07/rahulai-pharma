"""Audience variants for slide decks.

One Deep Deck run can spawn multiple variants tuned for different audiences:

    exec      — 5 slides, headlines only, 60-word notes, low jargon
    team      — 10 slides, full detail, 80-word notes, tactical
    external  — 8 slides, no internal/PII numbers, polished narrative
    board     — 12 slides + appendix, regulatory + risk emphasis

Each variant = own row in dash_presentations linked via parent_id.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# Per-audience tuning knobs (used in stage 6 prompt + post-filter)
AUDIENCE_PROFILES: Dict[str, Dict[str, Any]] = {
    "exec": {
        "label": "Exec",
        "target_slides": 5,
        "tone": "C-suite: tight, money-first, ask-driven. Skip how-we-got-here.",
        "notes_words": 60,
        "include_appendix": False,
        "strip_pii": False,
    },
    "team": {
        "label": "Team",
        "target_slides": 10,
        "tone": "Practitioners: detailed, tactical, include methodology.",
        "notes_words": 80,
        "include_appendix": False,
        "strip_pii": False,
    },
    "external": {
        "label": "External",
        "target_slides": 8,
        "tone": "Customers/partners: neutral, polished. No jargon, no internal metrics.",
        "notes_words": 70,
        "include_appendix": False,
        "strip_pii": True,  # strip internal-only numbers
    },
    "board": {
        "label": "Board",
        "target_slides": 12,
        "tone": "Board of directors: governance, risk, regulatory. Include appendix w/ raw data.",
        "notes_words": 90,
        "include_appendix": True,
        "strip_pii": False,
    },
    None: {
        "label": "Standard",
        "target_slides": 8,
        "tone": "Mixed audience: balanced.",
        "notes_words": 75,
        "include_appendix": False,
        "strip_pii": False,
    },
}


def get_profile(audience: Optional[str]) -> Dict[str, Any]:
    """Return tuning profile for audience name, fallback to Standard."""
    return AUDIENCE_PROFILES.get(audience) or AUDIENCE_PROFILES[None]


def tune_insight_pack_for_audience(insight_pack: Dict[str, Any],
                                    audience: Optional[str]) -> Dict[str, Any]:
    """Inject audience profile into insight pack so slide-agent picks it up."""
    profile = get_profile(audience)
    out = dict(insight_pack)
    out["audience"] = audience or "standard"
    out["audience_profile"] = profile
    # Trim recommendations for exec (top 3 only)
    if audience == "exec":
        recs = out.get("recommendations") or []
        out["recommendations"] = recs[:3]
    # Strip raw numbers for external (replace w/ relative %)
    if audience == "external":
        evid = out.get("supporting_evidence") or []
        out["supporting_evidence"] = [
            {**e, "value": _strip_internal_value(e.get("value", ""))}
            for e in evid
        ]
    return out


def _strip_internal_value(v: Any) -> Any:
    """For external audience: replace specific $ amounts with relative %.

    Cheap pass: if value contains $/¥/€ followed by digits, replace w/ "[$$$]".
    Caller can refine via LLM if needed.
    """
    s = str(v) if v is not None else ""
    import re
    if re.search(r"[$¥€£]\s*\d", s) or re.search(r"\b\d{4,}\b", s):
        return "(internal figure)"
    return v


def build_variant_message(audience: Optional[str], tuned_pack: Dict[str, Any]) -> str:
    """Synth message appended to slide-agent input so it knows audience constraints."""
    profile = tuned_pack.get("audience_profile") or {}
    return (
        f"AUDIENCE: {profile.get('label', 'Standard')}\n"
        f"TONE: {profile.get('tone', '')}\n"
        f"TARGET SLIDES: {profile.get('target_slides', 8)}\n"
        f"SPEAKER NOTES LENGTH: ~{profile.get('notes_words', 75)} words/slide\n"
        f"INCLUDE APPENDIX: {profile.get('include_appendix', False)}\n"
        + ("STRIP PII: yes — never quote raw internal $ amounts, use relative %/rank.\n"
           if profile.get('strip_pii') else "")
    )
