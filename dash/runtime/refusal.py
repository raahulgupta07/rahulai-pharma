"""Single source of truth for "was this chat turn refused?"

Sites that DECIDE to refuse call mark_refused(). Background tasks (memory
promoter, judge, KG, prefs, insights) call was_refused() BEFORE processing
the turn — instead of text-sentinel matching the answer (brittle vs custom
refusal messages / per-tenant phrasing / i18n).

Fail-soft everywhere — refusal-mark failure must never break the chat flow.
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
from typing import Optional

from sqlalchemy import text

logger = logging.getLogger(__name__)


def _engine():
    from db import get_sql_engine
    return get_sql_engine()


def _hash_question(question: str) -> str:
    """Normalize + sha1. Match on intent, not exact wording."""
    q = re.sub(r"\s+", " ", (question or "").strip().lower())[:500]
    return hashlib.sha1(q.encode("utf-8", errors="ignore")).hexdigest()[:24]


def mark_refused(
    session_id: Optional[str],
    question: str,
    source: str,
    reason: Optional[str] = None,
) -> bool:
    """Record that THIS session+question turn was refused. Idempotent on
    (session_id, question_hash) within ~60s window — duplicate marks for the
    same turn fire-and-forget.

    Args:
      session_id: agno session id (may be None — fall back to question-only)
      question:   user's question text
      source:     'scope_classifier' | 'agent_self' | 'stuck_loop' | 'text_sentinel'
      reason:     free-text reason (off_topic, denied_intent, …)

    Returns True if marked, False on any failure (still fail-soft).
    """
    if not session_id and not question:
        return False
    try:
        sid = session_id or "_anonymous"
        qhash = _hash_question(question)
        qprev = (question or "")[:200]
        eng = _engine()
        with eng.begin() as cn:
            cn.execute(text(
                "INSERT INTO dash.dash_refusal_marks "
                "  (session_id, question_hash, question_preview, source, reason) "
                "VALUES (:s, :h, :p, :src, :rsn)"
            ), {"s": sid, "h": qhash, "p": qprev,
                "src": source, "rsn": reason or ""})
        return True
    except Exception as e:
        logger.debug("mark_refused failed: %s", e)
        return False


def was_refused(
    session_id: Optional[str],
    question: Optional[str] = None,
    within_seconds: int = 120,
) -> Optional[dict]:
    """Return refusal-mark row if THIS session was refused recently.

    Strategy:
      - If question provided → match by (session_id, question_hash) — exact-turn
      - Else → match latest refusal in session within window
    Returns dict {source, reason, refused_at} or None.
    """
    if not session_id:
        return None
    try:
        sid = session_id
        eng = _engine()
        with eng.connect() as cn:
            if question:
                row = cn.execute(text(
                    "SELECT source, reason, refused_at "
                    "  FROM dash.dash_refusal_marks "
                    " WHERE session_id=:s AND question_hash=:h "
                    "   AND refused_at > NOW() - make_interval(secs => :w) "
                    " ORDER BY refused_at DESC LIMIT 1"
                ), {"s": sid, "h": _hash_question(question),
                    "w": within_seconds}).mappings().fetchone()
            else:
                row = cn.execute(text(
                    "SELECT source, reason, refused_at "
                    "  FROM dash.dash_refusal_marks "
                    " WHERE session_id=:s "
                    "   AND refused_at > NOW() - make_interval(secs => :w) "
                    " ORDER BY refused_at DESC LIMIT 1"
                ), {"s": sid, "w": within_seconds}).mappings().fetchone()
        return dict(row) if row else None
    except Exception as e:
        logger.debug("was_refused failed: %s", e)
        return None


# Backwards-compatible text-sentinel detector — kept as fallback for sessions
# that pre-date this module or where the refusal site couldn't be wired.
_REFUSAL_SENTINELS = (
    "i'm here to help with",
    "i can only help with",
    "i can't help with",
    "ask me things like",
    "this question is outside",
    "outside my scope",
    "stay focused on",
)


def is_refusal_text(answer: str) -> bool:
    """Last-resort fallback: detect refusal by sentinel-text matching."""
    if not answer:
        return False
    a = answer.lower()
    return any(s in a for s in _REFUSAL_SENTINELS)


__all__ = ["mark_refused", "was_refused", "is_refusal_text"]
