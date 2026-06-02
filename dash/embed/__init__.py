"""Embed module — agent widget for external sites.

Phase 0: skeleton + key generation.
Phase 1: CRUD manager.
Phase 2: HMAC + session.
Phase 3: chat endpoint + rate limit.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
from contextvars import ContextVar
from typing import Any

logger = logging.getLogger(__name__)


# ── Embed response style ContextVar ─────────────────────────────────────
# Set by the embed chat endpoint (app/embed_public.py) when the embed row's
# response_style column is 'consumer'. Read by dash/instructions.py to
# prepend a consumer-mode top-priority block that strips SQL/tags/agent
# names from the LLM output. Default None = no behavior change (developer
# mode or non-embed callers).
EMBED_RESPONSE_STYLE: ContextVar[str | None] = ContextVar(
    "embed_response_style", default=None
)


# ── Key generation ──────────────────────────────────────────────────────
def gen_embed_id() -> str:
    """Public identifier of an embed config. URL-safe, ~22 chars."""
    return "emb_" + secrets.token_urlsafe(16)


def gen_public_key() -> str:
    """Public key — safe in browser. Used to look up the embed."""
    return "pub_" + secrets.token_urlsafe(24)


def gen_secret_key() -> str:
    """Secret — given to host server-side ONCE. We persist a hash, never plaintext."""
    return "sk_" + secrets.token_urlsafe(32)


def hash_secret(secret: str) -> str:
    """Constant-time-comparable hash for storage."""
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def verify_secret(secret_plain: str, stored_hash: str) -> bool:
    return hmac.compare_digest(hash_secret(secret_plain), stored_hash)


def gen_session_token() -> str:
    """Short-lived chat session token (15 min TTL)."""
    return "sess_" + secrets.token_urlsafe(24)


# ── HMAC user signature (Phase 2) ───────────────────────────────────────
def hmac_user(secret: str, user_payload: dict) -> str:
    """Compute HMAC-SHA256 over canonicalized user payload.

    Host site computes this server-side using secret_key, passes signed
    payload to widget. Dash verifies on session create.
    """
    canon = json.dumps(user_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hmac.new(secret.encode("utf-8"), canon, hashlib.sha256).hexdigest()


def verify_hmac_user(secret: str, user_payload: dict, signature: str) -> bool:
    expected = hmac_user(secret, user_payload)
    return hmac.compare_digest(expected, signature)


# ── DB engine helper (matches skill_refinery pattern) ────────────────────
_ENGINE = None
_BOOTSTRAPPED = False


def _bootstrap_visibility_columns(engine) -> None:
    """Add bound_* policy columns if missing (idempotent inline migration)."""
    global _BOOTSTRAPPED
    if _BOOTSTRAPPED:
        return
    try:
        from sqlalchemy import text as _sa_text
        with engine.begin() as conn:
            conn.execute(_sa_text(
                "ALTER TABLE public.dash_agent_embeds "
                "ADD COLUMN IF NOT EXISTS bound_scope_id TEXT, "
                "ADD COLUMN IF NOT EXISTS bound_intent   TEXT DEFAULT 'public', "
                "ADD COLUMN IF NOT EXISTS bound_role     TEXT"
            ))
        _BOOTSTRAPPED = True
    except Exception as e:
        logger.warning("embed visibility-binding bootstrap skipped: %s", e)


def _get_engine():
    """NullPool engine that can write to public schema."""
    global _ENGINE
    if _ENGINE is None:
        from sqlalchemy import create_engine as _sa_create_engine
        from sqlalchemy.pool import NullPool
        from db import db_url
        _ENGINE = _sa_create_engine(db_url, poolclass=NullPool)
        _bootstrap_visibility_columns(_ENGINE)
    return _ENGINE


# ── Embed row-level security ContextVars (migration 064) ────────────────
# Re-exported from dash.embed.rls so callers can do `from dash.embed import
# EMBED_CLAIMS`. Canonical definitions live in rls.py so the enforcement
# engine doesn't need to import this module at load time.
from dash.embed.rls import (  # noqa: E402
    EMBED_CLAIMS,
    EMBED_RLS_POLICIES,
    EMBED_RLS_AUDIT_CTX,
)
