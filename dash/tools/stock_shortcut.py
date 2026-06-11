"""Deterministic stock fast-path — answer a *pure availability* question with NO
LLM round-trip.

WHY: every chat turn on this product runs the same gemini-3-flash model, and each
OpenRouter round-trip is ~5-6s. A tool-using answer needs ≥2 round-trips
(pick tool → compose answer) ≈ 12s. For the single most common counter question —
"do we have X in stock?" — that is pure waste: the answer is one `stock_check`
call + a fixed format. This module detects that narrow intent, calls `stock_check`
directly, and formats the reply in code (~0.3s, no model). It is intentionally
CONSERVATIVE: on ANY ambiguity (multi-part question, clinical wording, no clear
drug term, or zero matches) it returns None and the caller falls through to the
full agent. A wrong extraction can only yield 0 rows → fall-through, never a wrong
answer.

Masking: when `mask_qty=True` (consumer / non-private embeds) it shows availability
only (✅/❌) and hides quantity + cost — same policy the agent path enforces.
"""

from __future__ import annotations

import re
import time

# Wording that means the question is NOT a pure availability lookup — clinical,
# advisory, comparative, or multi-intent. If ANY of these appear, bail to the
# agent (it reasons over substitutes / indications / interactions / SQL).
_BAIL_PATTERNS = re.compile(
    r"\b(substitut|alternativ|replace|instead|cheaper|equivalent|generic for|"
    r"interact|side[- ]?effect|dosage|dose|composition|what is|used for|"
    r"treat|indication|symptom|contraindicat|expire|expiry|trend|compare|"
    r"versus|\bvs\b|total|how much value|revenue|sales|top \d|most|least|"
    r"average|count of|breakdown|report)\b",
    re.IGNORECASE,
)
# "and" / commas joining two asks → not a single clean lookup.
_MULTI = re.compile(r"\b(and|also|plus|then)\b|,", re.IGNORECASE)

# English availability carriers → capture the drug term.
_EN_PATTERNS = [
    re.compile(r"^\s*(?:do|does)\s+(?:we|you|i)\s+have\s+(?:any\s+)?(.+?)\s*(?:in stock|available|on hand|left|here)?\s*\??\s*$", re.I),
    re.compile(r"^\s*(?:is|are)\s+(?:there\s+)?(?:any\s+)?(.+?)\s+(?:in stock|available|on hand|left)\s*\??\s*$", re.I),
    re.compile(r"^\s*(?:do|does)\s+(?:we|you)\s+(?:carry|stock|sell|got|have got)\s+(?:any\s+)?(.+?)\s*\??\s*$", re.I),
    re.compile(r"^\s*(?:stock|availability)\s+(?:of|for|level of)\s+(.+?)\s*\??\s*$", re.I),
    re.compile(r"^\s*(.+?)\s+(?:in stock|stock level|availability)\s*\??\s*$", re.I),
    re.compile(r"^\s*check\s+(?:stock\s+(?:of|for)\s+)?(.+?)\s*\??\s*$", re.I),
]
# Burmese availability: "<X> ရှိလား / ရှိသလား / လက်ကျန် ရှိလား" (is X in stock?).
_MY_PATTERNS = [
    re.compile(r"^\s*(.+?)\s*(?:လက်ကျန်)?\s*ရှိ(?:သ)?လား\s*\??\s*$"),
    re.compile(r"^\s*(.+?)\s*လက်ကျန်\s*(?:ဘယ်လောက်)?\s*\??\s*$"),
]

# Tokens to strip from a captured term (leftover qualifiers).
_STRIP_WORDS = re.compile(r"\b(?:any|some|the|a|an|please|now|currently|right now|tablet|tablets|tab|tabs|medicine|medicines|drug|drugs)\b", re.I)


def _looks_burmese(s: str) -> bool:
    return any("က" <= ch <= "႟" for ch in s)


def _extract_term(message: str) -> str | None:
    msg = (message or "").strip()
    if not msg or len(msg) > 90:
        return None
    pats = _MY_PATTERNS if _looks_burmese(msg) else _EN_PATTERNS
    for pat in pats:
        m = pat.match(msg)
        if m:
            term = (m.group(1) or "").strip(" \t?.း")  # also strip Burmese ။
            term = _STRIP_WORDS.sub(" ", term).strip()
            term = re.sub(r"\s{2,}", " ", term)
            # Reject empty / too-generic captures.
            if not term or len(term) < 2:
                return None
            if term.lower() in ("medicine", "anything", "something", "it", "stock", "stuff"):
                return None
            return term
    return None


def _fmt_answer(res: dict, term: str, mask_qty: bool, burmese: bool) -> str:
    """Compact, scannable, language-neutral stock reply (no LLM)."""
    rows = res.get("results") or []
    rows = rows[:8]
    in_stock = [r for r in rows if r.get("in_stock")]
    lines: list[str] = []
    # one-line lead
    if burmese:
        lead = (f"✅ {term} — လက်ကျန် ရှိပါတယ်" if in_stock
                else f"❌ {term} — သင့်ဆိုင်မှာ လက်ကျန် မရှိပါ")
    else:
        lead = (f"✅ {term} — in stock" if in_stock
                else f"❌ {term} — not in stock at your branch")
    lines.append(lead)
    for r in rows:
        brand = r.get("brand") or "—"
        salt = r.get("salt") or ""
        qty = int(r.get("your_stock") or 0)
        cost = int(r.get("cost") or 0)
        tick = "✅" if r.get("in_stock") else "❌"
        seg = f"{tick} {brand}"
        if salt:
            seg += f" — {salt}"
        if not mask_qty:
            seg += f" — qty {qty:,}"
            if cost:
                seg += f" — cost {cost:,}"
        # cross-branch availability hint when out at own branch
        ob = r.get("other_branches") or []
        if not r.get("in_stock") and ob:
            sites = ", ".join(str(b.get("site")) for b in ob[:3] if b.get("site"))
            if sites:
                seg += (f" · also at {sites}" if not burmese
                        else f" · {sites} တွင် ရှိ")
        lines.append(seg)
    return "\n".join(lines)


def try_stock_shortcut(message: str, site_code: str = "", mask_qty: bool = False) -> dict | None:
    """Return a formatted stock answer dict, or None to fall through to the agent.

    dict: {"answer": str, "count": int, "in_stock": int, "elapsed_ms": int, "term": str}
    Returns None when the question is not a clean single-drug availability lookup,
    or when the lookup finds nothing (so the agent can try harder / explain).
    """
    msg = (message or "").strip()
    if not msg:
        return None
    # Guard: clinical / advisory / comparative / multi-intent → agent.
    if _BAIL_PATTERNS.search(msg) or _MULTI.search(msg):
        return None
    term = _extract_term(msg)
    if not term:
        return None
    t0 = time.monotonic()
    try:
        from dash.tools.pharma_shop_tool import stock_check
        res = stock_check(query=term, site_code=site_code or "")
    except Exception:
        return None
    if not isinstance(res, dict) or not res.get("ok"):
        return None
    if int(res.get("count") or 0) <= 0:
        return None  # nothing matched → let the agent explain / suggest
    burmese = _looks_burmese(msg)
    answer = _fmt_answer(res, term, mask_qty=mask_qty, burmese=burmese)
    return {
        "answer": answer,
        "count": int(res.get("count") or 0),
        "in_stock": int(res.get("in_stock_count") or 0),
        "elapsed_ms": int((time.monotonic() - t0) * 1000),
        "term": term,
    }
