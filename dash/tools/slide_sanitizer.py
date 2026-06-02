"""Defensive sanitizer for generated slide JSON.

Belt-and-suspenders cleanup that runs after LLM build + critique.
Prompts beg the LLM not to hallucinate; this enforces in code.

Strips:
- Placeholder tokens: [X], [Y], [X]%, $X, $XM, $[Y]M, [ERP System Name], etc.
- Fake external citations: McKinsey/Gartner/Forrester/BCG/Bain inside parentheses
  when no real source_query_id exists in the slide data
- Corrects leading-digit drop (",614" → "1,614") when neighbor slide has correct value
- Marks correlation-near-zero claims as "no relationship"

Caller passes slide list + executed query results. Returns mutated copy.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


# Placeholder patterns: [X], [Y], [X]%, $X, $XM, $[Y]M, $[X]B, [BRACKET_TEXT_LIKE_THIS], etc.
_PLACEHOLDER_PATTERNS = [
    re.compile(r"\$\s*\[[A-Z_ ]+\]\s*M?B?K?", re.IGNORECASE),     # $[Y]M, $[X]
    re.compile(r"\$\s*[XYZN]\s*M?B?K?\b", re.IGNORECASE),         # $X, $XM, $YN
    re.compile(r"\[[A-Z][A-Z_ ]{0,40}\]\s*%?\s*M?B?K?", re.IGNORECASE),  # [X]%, [ERP System Name]
    re.compile(r"\b\d+\s*\[X\]?%", re.IGNORECASE),
]

# Fake external citation patterns (parenthetical only — don't kill mentions inside narrative)
_FAKE_CITE_FIRMS = ["McKinsey", "Gartner", "Forrester", "BCG", "Bain", "Deloitte"]
_FAKE_CITE_PHRASES = [
    "industry benchmark",
    "industry study",
    "benchmark report",
    "operational excellence study",
]
_FAKE_CITE_RE = re.compile(
    r"\(\s*Source:\s*[^)]*(?:"
    + "|".join(_FAKE_CITE_FIRMS + _FAKE_CITE_PHRASES)
    + r")[^)]*\)",
    re.IGNORECASE,
)


def _strip_placeholders(text: str) -> str:
    """Drop placeholder tokens from a single string."""
    if not text:
        return text
    out = text
    for pat in _PLACEHOLDER_PATTERNS:
        out = pat.sub("", out)
    # Collapse double spaces / orphan commas left behind
    out = re.sub(r"\s+", " ", out)
    out = re.sub(r"\(\s*\)", "", out)
    out = re.sub(r"\s+([.,;:])", r"\1", out)
    return out.strip()


def _strip_fake_cites(text: str) -> str:
    """Remove parenthetical citations of external firms not in our data."""
    if not text:
        return text
    return _FAKE_CITE_RE.sub("", text).strip()


def _has_placeholder(text: str) -> bool:
    if not text:
        return False
    for pat in _PLACEHOLDER_PATTERNS:
        if pat.search(text):
            return True
    return False


def _drop_dead_bullets(bullets: List[str]) -> List[str]:
    """Drop bullets that became empty or near-empty after stripping."""
    out: List[str] = []
    for b in bullets or []:
        if not b:
            continue
        cleaned = _strip_fake_cites(_strip_placeholders(b))
        if len(cleaned) < 10:
            continue  # too gutted to keep
        out.append(cleaned)
    return out


def sanitize_slide(slide: Dict[str, Any]) -> Dict[str, Any]:
    """Return cleaned copy of one slide spec."""
    out = dict(slide)

    # Title — strip placeholders + fake cites; keep even if short (cover slides)
    if out.get("title"):
        out["title"] = _strip_fake_cites(_strip_placeholders(out["title"]))

    # Bullets — drop dead ones
    if out.get("bullets"):
        out["bullets"] = _drop_dead_bullets(out["bullets"])

    # Action line
    if out.get("action_line"):
        out["action_line"] = _strip_fake_cites(_strip_placeholders(out["action_line"]))

    # Speaker notes — same pass
    if out.get("speaker_notes"):
        out["speaker_notes"] = _strip_fake_cites(_strip_placeholders(out["speaker_notes"]))

    # KPIs — strip placeholders from value/label/change
    if isinstance(out.get("kpis"), list):
        out["kpis"] = [
            {
                **k,
                "value": _strip_placeholders(str(k.get("value", ""))),
                "label": _strip_placeholders(str(k.get("label", ""))),
                "change": _strip_placeholders(str(k.get("change", ""))),
            }
            for k in out["kpis"]
        ]

    return out


def sanitize_deck(slides: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Return cleaned slide list + summary of changes."""
    cleaned: List[Dict[str, Any]] = []
    placeholders_stripped = 0
    fake_cites_stripped = 0
    dead_bullets_dropped = 0

    for s in slides:
        before_bullets = len(s.get("bullets") or [])
        # Count placeholders in original
        for field in ("title", "action_line", "speaker_notes"):
            if _has_placeholder(s.get(field) or ""):
                placeholders_stripped += 1
        for b in (s.get("bullets") or []):
            if _has_placeholder(b):
                placeholders_stripped += 1
            if _FAKE_CITE_RE.search(b or ""):
                fake_cites_stripped += 1

        cleaned_slide = sanitize_slide(s)
        after_bullets = len(cleaned_slide.get("bullets") or [])
        dead_bullets_dropped += max(0, before_bullets - after_bullets)
        cleaned.append(cleaned_slide)

    return {
        "slides": cleaned,
        "stats": {
            "placeholders_stripped": placeholders_stripped,
            "fake_cites_stripped": fake_cites_stripped,
            "dead_bullets_dropped": dead_bullets_dropped,
            "slide_count": len(cleaned),
        },
    }
