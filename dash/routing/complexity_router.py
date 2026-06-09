"""Complexity Router — Feature A.

Classifies a chat message into one of three complexity tiers and maps the tier
to a model string. Mirrors the cache + fail-open style of
``dash/scope_classifier.py``.

    decision = classify_complexity(project_slug, message)
    decision["tier"]   # "LOOKUP" | "ANALYSIS" | "AGENTIC"
    decision["model"]  # LITE_MODEL | CHAT_MODEL | DEEP_MODEL
    decision["score"]  # 0..1
    decision["signals"]# list[str] of deterministic cues that fired
    decision["reason"] # short human-readable explanation
    decision["cached"] # True on a cache hit

Design:
  Tier-1 deterministic ($0) keyword/heuristic scoring produces a score in 0..1.
  If the score sits in an ambiguous band near a tier boundary, a single cheap
  LITE_MODEL call (task ``complexity_router``) is used as a tiebreak. Any failure
  in that path keeps the deterministic result (fail-open). On any unexpected
  exception the whole function falls open to ANALYSIS / CHAT_MODEL.

Dependency-light by design: only re, hashlib, time, logging.
"""
from __future__ import annotations

import hashlib
import logging
import re
import time

logger = logging.getLogger(__name__)

# ── Cache (mirrors scope_classifier) ─────────────────────────────────────────
CACHE_TTL_S = 300.0
CACHE_MAX = 2000
_CACHE: dict[str, tuple[dict, float]] = {}  # key -> (decision, ts)

# ── Tier boundaries on the deterministic 0..1 score ──────────────────────────
# TRIVIAL is detected by cue (greeting/ack), not by score — handled first.
# score < LOOKUP_MAX        → LOOKUP
# LOOKUP_MAX..ANALYSIS_MAX  → ANALYSIS
# ANALYSIS_MAX..AGENTIC_MAX → AGENTIC
# >= AGENTIC_MAX            → REASONING (heaviest multi-step)
LOOKUP_MAX = 0.34
ANALYSIS_MAX = 0.67
AGENTIC_MAX = 0.88
# Ambiguity band: if the score is within this distance of a boundary, ask LITE.
AMBIGUOUS_BAND = 0.08

_TIERS = ("TRIVIAL", "LOOKUP", "ANALYSIS", "AGENTIC", "REASONING", "ULTRA")

# ── TRIVIAL detection (greeting / ack / smalltalk → short-circuit the team) ──
_TRIVIAL_TOKENS = {
    "hi", "hii", "hello", "hey", "yo", "hiya", "sup", "thanks", "thank",
    "thx", "ty", "ok", "okay", "k", "kk", "cool", "nice", "great", "awesome",
    "good", "perfect", "got", "bye", "goodbye", "cya", "yes", "no", "yep",
    "nope", "yeah", "sure", "morning", "afternoon", "evening", "lol", "haha",
}
_TRIVIAL_PHRASES = (
    # Pure greetings / acks only — capability questions ("who are you",
    # "what can you do", "help") were REMOVED so they fall through to the
    # agent, which answers them as a pharmacist via _is_chitchat /
    # _chitchat_instructions (wired in app/projects.py).
    "thank you", "got it", "sounds good", "good morning", "good afternoon",
    "good evening", "how are you",
)
# Any of these means there's a real data ask → NOT trivial.
_DATA_HINT_RE = re.compile(
    r"\b(how many|count|list|show|what is|sum|total|average|avg|trend|compare|"
    r"why|forecast|predict|table|data|sales|revenue|customer|order|report)\b",
    re.IGNORECASE,
)

_TRIVIAL_REPLY = (
    "Hi! I'm your CityPharma assistant. Ask me about stock levels, drug info "
    "(composition, dose, substitutes), valuations, or categories across your "
    "branches — and I'll dig in."
)


def _is_trivial(norm: str) -> bool:
    """True for greetings/acks/smalltalk with no data ask. Conservative."""
    if not norm:
        return False
    if _DATA_HINT_RE.search(norm):
        return False
    if any(p in norm for p in _TRIVIAL_PHRASES):
        return True
    words = norm.replace("?", "").replace("!", "").replace(".", "").split()
    if not words or len(words) > 4:
        return False
    # Short message made entirely of greeting/ack tokens → trivial.
    return all(w.strip(",") in _TRIVIAL_TOKENS for w in words)

# ── Deterministic cue tables ─────────────────────────────────────────────────
_LOOKUP_CUES = (
    "how many", "how much", "count", "list", "show", "what is", "what's",
    "give me", "display", "number of", "total of", "find the",
)
_ANALYSIS_CUES = (
    "compare", " vs ", " vs.", "versus", "trend", "why", "explain",
    "breakdown", "break down", "correlat", "drop", "decline", "change",
    "growth", "difference between", "distribution", "over time", "root cause",
)
_AGENTIC_CUES = (
    "build", "plan", "step by step", "step-by-step", "and then", "forecast",
    "simulate", "workflow", "pipeline", "orchestrate", "end to end",
    "end-to-end", "design a", "strategy", "recommend a plan",
)
_CONJUNCTIONS = (" and ", " then ", " also ", "; ", " plus ", " as well as ")
# "by <dim>" / "per <dim>" grouping cue
_BY_DIM_RE = re.compile(r"\b(?:by|per|grouped by|split by)\s+\w+", re.IGNORECASE)
# "across N datasets/tables/...": multi-source agentic cue
_ACROSS_N_RE = re.compile(
    r"\bacross\s+(?:\d+|several|multiple|all)\b", re.IGNORECASE
)
_NUM_RE = re.compile(r"\b\d+(?:[.,]\d+)?\b")


def _norm(message: str) -> str:
    return re.sub(r"\s+", " ", (message or "")).strip().lower()


def _hash(project_slug: str, message: str) -> str:
    norm = _norm(message)
    return hashlib.sha1(
        f"{project_slug}::{norm}".encode("utf-8", "ignore")
    ).hexdigest()[:24]


def _cache_get(key: str) -> dict | None:
    now = time.monotonic()
    hit = _CACHE.get(key)
    if not hit:
        return None
    decision, ts = hit
    if now - ts > CACHE_TTL_S:
        _CACHE.pop(key, None)
        return None
    return decision


def _cache_put(key: str, decision: dict) -> None:
    if len(_CACHE) >= CACHE_MAX:
        # Drop oldest 100 by timestamp.
        oldest = sorted(_CACHE.items(), key=lambda kv: kv[1][1])[:100]
        for k, _ in oldest:
            _CACHE.pop(k, None)
    _CACHE[key] = (decision, time.monotonic())


def _tier_for_score(score: float) -> str:
    if score < LOOKUP_MAX:
        return "LOOKUP"
    if score < ANALYSIS_MAX:
        return "ANALYSIS"
    if score < AGENTIC_MAX:
        return "AGENTIC"
    return "REASONING"


def _model_for_tier(tier: str) -> str:
    # Live-resolve via DB settings (UI-editable) → env fallback → default.
    from dash.settings import (
        get_lite_model, get_mid_model, get_deep_model,
        get_reasoning_model, get_ultra_model,
    )

    return {
        "TRIVIAL": get_lite_model(),
        "LOOKUP": get_lite_model(),
        "ANALYSIS": get_mid_model(),
        "AGENTIC": get_deep_model(),
        "REASONING": get_reasoning_model(),
        "ULTRA": get_ultra_model(),
    }.get(tier, get_mid_model())


def _score_deterministic(norm: str) -> tuple[float, list[str], bool]:
    """Return (score 0..1, signals, agentic_signal) from $0 keyword scoring.

    ``agentic_signal`` is True only when a genuine multi-step cue is present
    (agentic keyword, multi-source "across N", 2+ chained conjunctions, or a
    very long prompt). It is a HARD GATE: AGENTIC is impossible without it,
    even if the LLM tiebreak wants to escalate.
    """
    signals: list[str] = []
    score = 0.30  # neutral starting point (low ANALYSIS / high LOOKUP edge)

    lookup_hits = [c for c in _LOOKUP_CUES if c in norm]
    analysis_hits = [c for c in _ANALYSIS_CUES if c in norm]
    agentic_hits = [c for c in _AGENTIC_CUES if c in norm]

    for c in lookup_hits:
        signals.append(f"lookup:{c.strip()}")
    for c in analysis_hits:
        signals.append(f"analysis:{c.strip()}")
    for c in agentic_hits:
        signals.append(f"agentic:{c.strip()}")

    # LOOKUP cues pull the score down (simpler).
    score -= 0.12 * len(lookup_hits)
    # ANALYSIS cues pull toward the middle, with diminishing returns + a cap so a
    # richly-worded analysis question (compare + vs + explain + drop) can't stack
    # its way past the AGENTIC boundary on analysis cues alone.
    score += min(0.30, 0.12 * len(analysis_hits))
    # AGENTIC cues pull strongly up.
    score += 0.30 * len(agentic_hits)

    # "by <dim>" grouping → analysis flavor.
    if _BY_DIM_RE.search(norm):
        signals.append("by_dim")
        score += 0.10

    # "across N datasets" → multi-source agentic.
    across_n = bool(_ACROSS_N_RE.search(norm))
    if across_n:
        signals.append("across_n")
        score += 0.22

    # Conjunctions: a single "and"/"then" joining analysis verbs is still ANALYSIS
    # (e.g. "compare X and explain Y"). Only TWO+ chained steps add agentic weight.
    conj_count = sum(norm.count(c) for c in _CONJUNCTIONS)
    if conj_count >= 1:
        signals.append(f"conjunction:{conj_count}")
    if conj_count >= 2:
        score += 0.10 * conj_count

    # Very long message → likely multi-part / agentic.
    words = len(norm.split())
    if words >= 60:
        signals.append("very_long")
        score += 0.18
    elif words >= 35:
        signals.append("long")
        score += 0.08
    elif words <= 6:
        signals.append("short")
        score -= 0.08

    # Hard ceiling: AGENTIC requires a genuine multi-step signal (an agentic cue,
    # multi-source "across N", 2+ chained conjunctions, or a very long prompt).
    # Without one, cap below the AGENTIC boundary so analysis stays ANALYSIS.
    agentic_signal = bool(agentic_hits) or across_n or conj_count >= 2 or words >= 60
    if not agentic_signal:
        ceiling = ANALYSIS_MAX - 0.02
        if score > ceiling:
            signals.append("no_agentic_ceiling")
            score = ceiling

    # A bare single-metric question (single number, no conjunction, lookup cue,
    # short) is a strong LOOKUP signal.
    if (
        lookup_hits
        and not analysis_hits
        and not agentic_hits
        and conj_count == 0
        and len(_NUM_RE.findall(norm)) <= 1
        and words <= 14
    ):
        signals.append("single_metric")
        score -= 0.18

    # Clamp.
    score = max(0.0, min(1.0, score))
    return score, signals, agentic_signal


def _near_boundary(score: float) -> bool:
    for b in (LOOKUP_MAX, ANALYSIS_MAX):
        if abs(score - b) <= AMBIGUOUS_BAND:
            return True
    return False


_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)
_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_llm_json(raw: str) -> dict | None:
    """Robustly parse the LITE LLM JSON reply. None on any failure."""
    import json

    if not raw:
        return None
    txt = _FENCE_RE.sub("", raw).strip()
    try:
        return json.loads(txt)
    except Exception:
        pass
    m = _OBJ_RE.search(txt)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


_LLM_PROMPT = """You classify how complex a data-analytics question is.

Tiers:
- LOOKUP: a single metric/fact (count, list, "what is X"). Simplest.
- ANALYSIS: comparison, trend, breakdown, "why", correlation, grouped query.
- AGENTIC: multi-step (build/plan/forecast + explain, across multiple datasets,
  "and then", step by step). Most complex.

Question:
{question}

Return STRICT JSON only, no prose:
{{"tier": "LOOKUP|ANALYSIS|AGENTIC", "score": 0.0, "reason": "short"}}"""


def _llm_tiebreak(message: str) -> dict | None:
    """Single cheap LITE_MODEL call. Returns parsed dict or None (fail-open)."""
    try:
        from dash.settings import training_llm_call
    except Exception as e:  # pragma: no cover
        logger.warning("complexity_router: training_llm_call import failed: %s", e)
        return None
    try:
        raw = training_llm_call(
            _LLM_PROMPT.format(question=(message or "")[:600]),
            "complexity_router",
        )
    except Exception as e:
        logger.warning("complexity_router: LLM call failed: %s", e)
        return None
    parsed = _parse_llm_json(raw or "")
    if not parsed:
        return None
    tier = str(parsed.get("tier", "")).strip().upper()
    if tier not in _TIERS:
        return None
    try:
        score = float(parsed.get("score"))
    except (TypeError, ValueError):
        score = None
    reason = str(parsed.get("reason", ""))[:200]
    return {"tier": tier, "score": score, "reason": reason}


def classify_complexity(
    project_slug: str,
    message: str,
    *,
    session_id: str | None = None,
) -> dict:
    """Classify message complexity. Always returns a dict; never raises."""
    try:
        from dash.settings import CHAT_MODEL  # local import; cheap

        if not message or not message.strip():
            return {
                "tier": "LOOKUP",
                "score": 0.0,
                "signals": ["empty"],
                "model": _model_for_tier("LOOKUP"),
                "reason": "empty message",
                "cached": False,
            }

        key = _hash(project_slug or "", message)
        cached = _cache_get(key)
        if cached is not None:
            out = dict(cached)
            out["cached"] = True
            return out

        norm = _norm(message)

        # TRIVIAL: greeting/ack/smalltalk → short-circuit the team entirely.
        if _is_trivial(norm):
            decision = {
                "tier": "TRIVIAL",
                "score": 0.0,
                "signals": ["trivial"],
                "model": _model_for_tier("TRIVIAL"),
                "reason": "greeting/ack — no data ask",
                "cached": False,
                "short_circuit": True,
                "reply": _TRIVIAL_REPLY,
            }
            _cache_put(key, decision)
            return decision

        score, signals, agentic_signal = _score_deterministic(norm)
        tier = _tier_for_score(score)
        reason = f"deterministic: score={score:.2f} → {tier}"

        # Ambiguous band → ask LITE once. Keep deterministic on any failure.
        if _near_boundary(score):
            signals.append("ambiguous_band")
            llm = _llm_tiebreak(message)
            if llm:
                tier = llm["tier"]
                if llm.get("score") is not None:
                    score = max(0.0, min(1.0, float(llm["score"])))
                signals.append("llm_tiebreak")
                reason = "llm tiebreak: " + (llm.get("reason") or tier)

        # ULTRA escalation: the hardest class — multi-DATASET ("across N") AND
        # 2+ distinct agentic verbs (e.g. build + forecast across 3 datasets).
        if tier in ("AGENTIC", "REASONING"):
            _n_ag = sum(s.startswith("agentic:") for s in signals)
            if ("across_n" in signals) and _n_ag >= 2:
                tier = "ULTRA"
                signals.append("ultra_escalated")

        # Hard gate: AGENTIC/REASONING/ULTRA require a genuine multi-step cue. Block
        # the LLM (or a stacked-analysis score) from escalating without one.
        if tier in ("AGENTIC", "REASONING", "ULTRA") and not agentic_signal:
            tier = "ANALYSIS"
            score = min(score, ANALYSIS_MAX - 0.02)
            signals.append("agentic_blocked")
            reason += " (no multi-step cue — capped to ANALYSIS)"

        decision = {
            "tier": tier,
            "score": round(float(score), 4),
            "signals": signals,
            "model": _model_for_tier(tier),
            "reason": reason,
            "cached": False,
        }
        _cache_put(key, decision)
        return decision

    except Exception as e:
        logger.warning("complexity_router: fail-open: %s", e)
        try:
            from dash.settings import CHAT_MODEL

            _model = CHAT_MODEL
        except Exception:
            _model = "google/gemini-3-flash-preview"
        return {
            "tier": "ANALYSIS",
            "model": _model,
            "score": 0.5,
            "signals": ["fallback"],
            "reason": str(e)[:120],
            "cached": False,
        }
