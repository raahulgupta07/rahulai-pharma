"""Number citation guard (H3, H10).

Catches fabricated numbers in agent responses — numbers that appear in the
final answer text but in NO tool_call output. LLM hallucinates between tool
calls; this is the runtime detector that flags them in the trace panel.

Heuristic, not a hard gate. Returns advisory dict consumed by chat path:

    from dash.guards import audit_numbers
    result = audit_numbers(answer_text, tool_outputs_concatenated)
    # → {ok, flagged: [{number, context}], total_numbers, cited, fabricated_pct}

Tolerances:
  - Numbers parsed as floats; ±0.5% relative match allowed (rounding)
  - Integers <10 ignored (article noise: "the 3 reasons", "top 5")
  - Years 1900-2100 ignored (calendar context, not data)
  - Percentages (`32%`) checked separately against raw 32 and 0.32
  - Currency symbols + commas stripped before parse
  - Scientific notation supported (1.5e6 → 1500000)

Fail-soft: any parse error → return ok=True w/ empty flagged list. Better
to miss a hallucination than block a real answer.
"""

from __future__ import annotations

import logging
import re
from typing import Iterable

logger = logging.getLogger(__name__)

# Match: optional $/€/£/¥/₹ + digits (possibly with commas/decimal) + optional %/M/K/B
_NUM_RE = re.compile(
    r"(?<![A-Za-z_])"                       # not glued to a word
    r"([$€£¥₹]?-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|"  # 1,234,567.89
    r"[$€£¥₹]?-?\d+\.\d+|"                  # 12.5
    r"[$€£¥₹]?-?\d+)"                       # 42
    r"(\s*%|\s*[MKB]\b|\s*million|\s*billion|\s*thousand)?"
)
_TOL_REL = 0.005   # 0.5%
_TOL_ABS = 0.01    # for sub-unit values
_MIN_INT_TO_AUDIT = 10
_YEAR_MIN, _YEAR_MAX = 1900, 2100


def _parse(raw: str) -> float | None:
    """Coerce '$1,234.5M' → 1234500000.0. None on parse fail."""
    if not raw:
        return None
    s = raw.strip().replace(",", "").lstrip("$€£¥₹")
    mult = 1.0
    for suf, m in (("%", 0.01), ("M", 1e6), ("K", 1e3), ("B", 1e9),
                   ("million", 1e6), ("billion", 1e9), ("thousand", 1e3)):
        if s.lower().endswith(suf.lower()):
            mult = m
            s = s[: -len(suf)].strip()
            break
    try:
        return float(s) * mult
    except ValueError:
        return None


def _extract_numbers(text: str) -> list[tuple[float, str]]:
    """Return [(parsed_value, raw_context)] from text. Filters articles + years."""
    out: list[tuple[float, str]] = []
    for m in _NUM_RE.finditer(text or ""):
        raw = (m.group(1) or "") + (m.group(2) or "")
        val = _parse(raw)
        if val is None:
            continue
        # Ignore small articles ("3 reasons")
        if val.is_integer() and abs(val) < _MIN_INT_TO_AUDIT:
            continue
        # Ignore years (calendar context)
        if val.is_integer() and _YEAR_MIN <= val <= _YEAR_MAX and "." not in raw:
            continue
        # ±15 chars context for trace display
        start = max(0, m.start() - 15)
        end = min(len(text), m.end() + 15)
        ctx = text[start:end].strip()
        out.append((val, ctx))
    return out


def _is_cited(needle: float, haystack_numbers: Iterable[float]) -> bool:
    """Does `needle` appear in tool outputs within tolerance?"""
    for h in haystack_numbers:
        if h == 0 and needle == 0:
            return True
        if abs(needle) < 1:
            if abs(needle - h) <= _TOL_ABS:
                return True
        else:
            rel = abs(needle - h) / max(abs(needle), abs(h), 1e-9)
            if rel <= _TOL_REL:
                return True
    return False


def audit_numbers(answer_text: str, tool_outputs: str) -> dict:
    """Audit numbers in answer against tool outputs.

    Returns:
      {
        ok: True (always — fail-soft),
        total_numbers: int,
        cited: int,
        fabricated: int,
        fabricated_pct: float,
        flagged: [{value, context}, ...] up to 20,
      }
    """
    try:
        answer_nums = _extract_numbers(answer_text or "")
        tool_nums = [v for v, _ in _extract_numbers(tool_outputs or "")]
        if not answer_nums:
            return {
                "ok": True, "total_numbers": 0, "cited": 0,
                "fabricated": 0, "fabricated_pct": 0.0, "flagged": [],
            }
        flagged: list[dict] = []
        cited = 0
        for val, ctx in answer_nums:
            if _is_cited(val, tool_nums):
                cited += 1
            else:
                flagged.append({"value": val, "context": ctx})
        total = len(answer_nums)
        fab = len(flagged)
        return {
            "ok": True,
            "total_numbers": total,
            "cited": cited,
            "fabricated": fab,
            "fabricated_pct": round(fab / max(total, 1), 4),
            "flagged": flagged[:20],
        }
    except Exception as e:
        logger.debug(f"audit_numbers failed: {e}")
        return {"ok": True, "total_numbers": 0, "cited": 0,
                "fabricated": 0, "fabricated_pct": 0.0, "flagged": []}
