"""Text sanitizer — strips raw numbers from narrative when intent != private.

Defensive: regex only, never raise. Returns original text on any error.
"""
from __future__ import annotations
import logging
import re

logger = logging.getLogger(__name__)

# WHY: thresholds prevent banding tiny numbers (1-3) where banding adds no privacy value
_BAND_THRESHOLD = 5

_PATTERNS = [
    # currency: $1,234.56 / EUR 47 / £100
    (re.compile(r'(?<![A-Za-z])(?:\$|€|£|EUR|USD|GBP|JPY|¥)\s?[\d,]+(?:\.\d+)?', re.I), "[banded]"),
    # qty/units patterns: "47 units", "qty 47", "quantity: 1234"
    (re.compile(r'\b(?:qty|quantity|units?|stock|inventory|count)\s*[:=]?\s*\d[\d,]*\b', re.I), "[banded]"),
    (re.compile(r'\b\d[\d,]*\s+units?\b', re.I), "[banded]"),
    # bare large numbers (>= threshold): only when standalone, not percentages or years
    (re.compile(r'(?<![\d.,%])\b(\d{1,3}(?:,\d{3})+|\d{2,})\b(?!\s*%|\s*(?:19|20)\d{2})'), None),  # special handler
]


def sanitize_narrative(text: str, project_slug: str, intent: str) -> str:
    """Replace raw numbers with banded markers when intent restricts visibility.

    intent='private' is passthrough. 'network'/'public' → strip large numbers.
    Never raises; returns original on error.
    """
    if not text or not isinstance(text, str):
        return text or ""
    if intent == "private":
        return text
    try:
        out = text
        for pat, repl in _PATTERNS:
            if repl is None:
                # bare number handler — only band if value >= threshold
                def _swap(m: re.Match) -> str:
                    raw = m.group(0).replace(",", "")
                    try:
                        val = float(raw)
                        # WHY: skip 4-digit years (1900-2100) — temporal context, not sensitive qty
                        if len(raw) == 4 and 1900 <= val <= 2100 and "." not in raw:
                            return m.group(0)
                        if val >= _BAND_THRESHOLD:
                            return "[banded]"
                    except ValueError:
                        pass
                    return m.group(0)
                out = pat.sub(_swap, out)
            else:
                out = pat.sub(repl, out)
        return out
    except Exception as e:
        logger.warning("sanitize_narrative failed (%s); returning original", e)
        return text
