"""Teach-by-chat (Build A) — let a user durably teach the agent mid-conversation.

When a user message carries an explicit TEACH / CORRECTION intent ("remember:",
"from now on", "correction:", "note that", Burmese မှတ်ထား…), distil ONE durable
fact from it and store it as a ``status='pending'`` memory in
``public.dash_memories`` — the SAME review queue the distiller daemon and OKF
import use (the Intern Rule). It does NOT reach chat until an admin approves it
in Agent Brain → Memories. This retires the manual OKF-import step for everyday,
one-off facts: the user just says "remember X" in chat.

REACTIVE by design — normal questions store NOTHING. The ``wants_to_teach`` gate
is a zero-cost regex that fires on explicit teaching INTENT only, so the LLM
extraction (the only cost) runs on a tiny fraction of turns. Mirrors the proven
distiller pattern (small LLM extract → pending memory) in [dash.learning.distiller].
"""
from __future__ import annotations

import logging
import os
import re

log = logging.getLogger("dash.chat_teach")

# Explicit teach / correction intent. EN + Burmese. Fires on INTENT only — a
# plain analytical question ("how many … ?") matches nothing here, so it writes
# nothing and costs nothing. Keep triggers IMPERATIVE / declarative, not
# interrogative, to avoid learning from questions.
_TEACH_RE = re.compile(
    r"\b("
    r"remember(?:\s+that|\s+this)?|note\s+that|keep\s+in\s+mind|for\s+future|"
    r"from\s+now\s+on|going\s+forward|correction|to\s+be\s+clear|please\s+note|"
    r"make\s+a\s+note|don'?t\s+forget|always\s+(?:treat|use|assume)|"
    r"never\s+(?:treat|use|assume)|just\s+so\s+you\s+know|fyi\b"
    r")\b"
    r"|မှတ်ထား|မှတ်သားထား|မှတ်သားပါ|သတိထား",
    re.IGNORECASE,
)

_EXTRACT_PROMPT = """A pharmacy staff user is TEACHING the assistant a durable fact or policy to remember for future questions. Extract ONE short, durable, generalisable fact from their message — a rule, a definition, a mapping, a classification, or a preference. Strip lead-in filler ("remember", "note that", "from now on"). KEEP specifics: names, site codes, branches, numbers, conditions. If the message contains no teachable durable fact (it is just a question, greeting, or chit-chat), reply exactly NONE.

User message: {msg}

Reply with ONE declarative sentence (the fact to store), or NONE. No preamble, no quotes."""


def wants_to_teach(message: str) -> bool:
    """Zero-cost gate: True only when the message shows explicit teach intent."""
    if not message or len(message.strip()) < 6:
        return False
    return bool(_TEACH_RE.search(message))


def _extract(message: str) -> str | None:
    """LLM → one durable fact, or None. Fail-soft (no key / over budget → None)."""
    try:
        from dash.settings import training_llm_call
        out = training_llm_call(_EXTRACT_PROMPT.format(msg=message[:600]), task="extraction")
        if not out:
            return None
        fact = out.strip().strip('"').strip()
        if not fact or fact.upper().startswith("NONE") or len(fact) < 8:
            return None
        return fact[:300]
    except Exception as e:  # noqa: BLE001
        log.debug(f"chat_teach extract failed: {e}")
        return None


def _conn():
    from dash.tools._direct_db import direct_connect
    c = direct_connect(
        host=os.getenv("GRAPH_DB_HOST", "dash-db"),
        port=int(os.getenv("GRAPH_DB_PORT", "5432")),
        user=os.getenv("DB_USER", "ai"),
        dbname=os.getenv("DB_DATABASE", "ai"),
        password=os.getenv("DB_PASS", ""),
        connect_timeout=8,
        autocommit=True,
    )
    cur = c.cursor()
    cur.execute("SET statement_timeout = '30s';")
    return c, cur


def maybe_learn(
    slug: str,
    question: str,
    answer: str | None = None,
    user_id=None,
    conv_id: str | None = None,
) -> dict | None:
    """Distil + store a pending memory IFF the user message is an explicit teach.

    Returns ``{"id": int, "fact": str}`` when a fact was saved (status='pending',
    awaiting admin approval), else ``None``. Normal questions return None and
    write nothing. Safe to call fire-and-forget on every chat turn — the gate
    short-circuits the cost.
    """
    if not slug or not wants_to_teach(question or ""):
        return None
    fact = _extract(question)
    if not fact:
        return None
    # user_id may arrive as a non-int (anon/widget). Only bind it when numeric.
    try:
        _uid = int(user_id) if user_id is not None else None
    except (TypeError, ValueError):
        _uid = None
    try:
        c, cur = _conn()
        try:
            cur.execute(
                "INSERT INTO public.dash_memories "
                "(project_slug, user_id, scope, fact, source, status, created_by) "
                "VALUES (%s, %s, 'project', %s, 'chat_teach', 'pending', %s) "
                "RETURNING id",
                (slug, _uid, fact, f"chat_teach:{user_id or 'anon'}"),
            )
            row = cur.fetchone()
            mid = row[0] if row else None
            log.info(f"chat_teach saved pending memory #{mid} for {slug}: {fact[:80]}")
            return {"id": mid, "fact": fact}
        finally:
            c.close()
    except Exception as e:  # noqa: BLE001
        log.warning(f"chat_teach maybe_learn failed for {slug}: {e}")
        return None
