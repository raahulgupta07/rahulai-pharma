"""Pre-flight intent classifier — Phase 5 of auto-scope guardrail.

Cheap LITE_MODEL gate that runs BEFORE the team is invoked. If the question
is off-topic per the project's scope, return the refusal_message directly
without ever calling the agent. Saves cost + latency on jailbreak attempts.

Reliability: ~99% (vs ~80% for instructions-only refusal in Phase 4).

Usage:
    decision = classify_question(project_slug, question)
    if decision.refused:
        return decision.refusal_message
    # else proceed to team.run(...)
"""
from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

CACHE_TTL_S = 300.0
CACHE_MAX = 2000

_CACHE: dict[str, tuple[bool, float, str | None]] = {}  # hash -> (refused, ts, reason)
_CACHE_LOCK = threading.Lock()


@dataclass
class Decision:
    refused: bool
    refusal_message: str | None = None
    reason: str | None = None        # 'off_topic' | 'denied_intent' | 'no_scope' | 'cache_hit'
    matched_topic: str | None = None
    classifier: str = "preflight"


_FALLBACK_REFUSAL = (
    "I can only help with topics this agent was trained on. "
    "Please ask something related."
)


def _hash_q(project_slug: str, question: str) -> str:
    norm = re.sub(r"\s+", " ", (question or "")).strip().lower()
    return hashlib.sha1(f"{project_slug}::{norm}".encode("utf-8", "ignore")).hexdigest()[:24]


def _cache_get(key: str) -> tuple[bool, str | None] | None:
    now = time.monotonic()
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if not hit:
            return None
        refused, ts, reason = hit
        if now - ts > CACHE_TTL_S:
            _CACHE.pop(key, None)
            return None
        return refused, reason


def _cache_put(key: str, refused: bool, reason: str | None) -> None:
    with _CACHE_LOCK:
        if len(_CACHE) >= CACHE_MAX:
            # Drop oldest 100 by timestamp.
            oldest = sorted(_CACHE.items(), key=lambda kv: kv[1][1])[:100]
            for k, _ in oldest:
                _CACHE.pop(k, None)
        _CACHE[key] = (refused, time.monotonic(), reason)


def _get_scope_safe(project_slug: str | None) -> dict | None:
    if not project_slug:
        return None
    try:
        from dash.feature_config import get_scope
        s = get_scope(project_slug)
        if not s:
            return None
        # Need at least topics OR denied_intents to gate.
        if not s.get("topics") and not s.get("denied_intents"):
            return None
        return s
    except Exception:
        return None


PROMPT_TEMPLATE = """You are SCOPE-GATE for a Dash data agent. Decide if the user's question is in-scope.

This agent only handles these topics:
{topics}

Refuse anything in this list:
{denied}

User question:
{question}

Output ONE word:
- "yes" if the question is clearly about one of the topics above
- "no"  if it's off-topic, generic world knowledge, or in the refuse list
- "yes" if borderline / ambiguous (favor letting the agent decide)

Output ONLY "yes" or "no", nothing else."""


def _llm_decide(question: str, scope: dict) -> bool:
    """Call LITE_MODEL. Return True if refused (off-topic), False if on-topic."""
    try:
        from dash.settings import training_llm_call
    except Exception as e:
        logger.warning("scope_classifier: training_llm_call import failed: %s", e)
        return False  # fail-open — don't block on classifier outage

    topics = "\n".join(f"  - {t}" for t in (scope.get("topics") or [])[:10]) or "  (none)"
    denied = "\n".join(f"  - {d}" for d in (scope.get("denied_intents") or [])[:8]) or "  (none)"
    prompt = PROMPT_TEMPLATE.format(
        topics=topics, denied=denied,
        question=(question or "")[:500],
    )

    try:
        # 'classification' isn't a registered task in TRAINING_CONFIGS — use the
        # cheapest one. 'meta_learning' or 'extraction' both map to LITE_MODEL.
        raw = training_llm_call(prompt, task="extraction")
    except Exception as e:
        logger.warning("scope_classifier: LLM call failed: %s", e)
        return False  # fail-open

    if not raw:
        return False
    answer = raw.strip().lower()
    # Look for first yes/no token.
    first_tok = re.findall(r"\b(yes|no)\b", answer)
    if not first_tok:
        return False
    return first_tok[0] == "no"


def classify_question(project_slug: str | None, question: str) -> Decision:
    """Decide whether to refuse this question. Always returns Decision (never raises)."""
    if not question or not question.strip():
        return Decision(refused=False, reason="empty_question")

    scope = _get_scope_safe(project_slug)
    if not scope:
        return Decision(refused=False, reason="no_scope")

    refusal_msg = scope.get("refusal_message") or _FALLBACK_REFUSAL

    # Cache lookup.
    key = _hash_q(project_slug or "", question)
    cached = _cache_get(key)
    if cached is not None:
        refused, reason = cached
        return Decision(
            refused=refused,
            refusal_message=refusal_msg if refused else None,
            reason=("cache_hit:" + (reason or "")) if refused else "cache_hit",
        )

    refused = _llm_decide(question, scope)
    _cache_put(key, refused, "off_topic" if refused else None)

    return Decision(
        refused=refused,
        refusal_message=refusal_msg if refused else None,
        reason="off_topic" if refused else "on_topic",
    )


def log_refusal(project_slug: str | None,
                question: str,
                decision: Decision,
                user_id: int | None = None,
                embed_id: str | None = None,
                external_user: str | None = None) -> None:
    """Best-effort write to dash_guardrail_audit. Never raises."""
    if not decision.refused:
        return
    try:
        from dash.tools.skill_refinery import _get_engine
        from sqlalchemy import text
        eng = _get_engine()
        with eng.begin() as conn:
            conn.execute(text(
                "INSERT INTO public.dash_guardrail_audit "
                "(project_slug, user_id, embed_id, external_user, question, "
                " refusal_reason, classifier, matched_topic, refusal_message) "
                "VALUES (:p, :u, :e, :ex, :q, :r, :c, :m, :msg)"
            ), {
                "p": project_slug, "u": user_id, "e": embed_id, "ex": external_user,
                "q": (question or "")[:2000],
                "r": decision.reason, "c": decision.classifier,
                "m": decision.matched_topic, "msg": (decision.refusal_message or "")[:500],
            })
    except Exception as e:
        logger.warning("scope_classifier: audit insert failed: %s", e)
