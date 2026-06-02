"""Embed session management — Phase 2.

Functions for verifying origin/HMAC and creating/validating short-lived
session tokens for the agent embed widget.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text

from dash.embed import _get_engine, gen_session_token, verify_hmac_user

logger = logging.getLogger(__name__)


# ── Origin verification ────────────────────────────────────────────────
def verify_origin(allowed_origins: list[str], request_origin: str | None,
                   server_origin: str | None = None) -> bool:
    """Exact-match the request Origin against the allowed list.

    Same-origin requests (request_origin == server_origin) auto-allow — this
    enables the dashboard's live-preview iframe to chat with the widget without
    requiring the dashboard's own URL in every embed's allowlist.

    Empty allowed_origins still denies cross-site requests.
    """
    if not request_origin:
        return False
    if server_origin and request_origin == server_origin:
        return True
    if not allowed_origins:
        return False
    return request_origin in set(allowed_origins)


# ── HMAC user payload verification ─────────────────────────────────────
def verify_user_payload(secret_key: str, user_payload: dict | None, signature: str | None) -> bool:
    """Wrap verify_hmac_user with a missing-input guard."""
    if not user_payload or not signature or not secret_key:
        return False
    try:
        return verify_hmac_user(secret_key, user_payload, signature)
    except Exception as e:
        logger.warning(f"verify_user_payload failed: {e}")
        return False


# ── Session create / validate / revoke ─────────────────────────────────
def create_session(
    embed_id: str,
    external_user: str | None,
    user_attrs: dict | None,
    origin: str | None,
    ip: str | None,
    ttl_minutes: int = 15,
) -> str:
    """Insert a new session row and return the session_token."""
    import json as _json

    token = gen_session_token()
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)
    user_attrs_json = _json.dumps(user_attrs) if user_attrs is not None else None

    eng = _get_engine()
    with eng.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO public.dash_embed_sessions
                    (embed_id, session_token, external_user, user_attrs, origin, ip, expires_at)
                VALUES
                    (:embed_id, :token, :external_user, CAST(:user_attrs AS JSONB),
                     :origin, :ip, :expires_at)
                """
            ),
            {
                "embed_id": embed_id,
                "token": token,
                "external_user": external_user,
                "user_attrs": user_attrs_json,
                "origin": origin,
                "ip": ip,
                "expires_at": expires_at,
            },
        )
    return token


def validate_session(session_token: str) -> dict | None:
    """Look up session, return None if missing/revoked/expired.

    On hit: bumps embed.last_used_at + increments session.request_count.
    Returns dict {embed_id, external_user, user_attrs, expires_at, request_count}.
    """
    if not session_token:
        return None

    eng = _get_engine()
    with eng.begin() as conn:
        row = conn.execute(
            text(
                """
                SELECT embed_id, external_user, user_attrs, expires_at, revoked, request_count
                FROM public.dash_embed_sessions
                WHERE session_token = :tok
                """
            ),
            {"tok": session_token},
        ).fetchone()

        if row is None:
            return None
        if row.revoked:
            return None
        # Compare in UTC
        now = datetime.now(timezone.utc)
        exp = row.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp <= now:
            return None

        # Bump counters
        conn.execute(
            text(
                """
                UPDATE public.dash_embed_sessions
                SET request_count = request_count + 1
                WHERE session_token = :tok
                """
            ),
            {"tok": session_token},
        )
        conn.execute(
            text(
                """
                UPDATE public.dash_agent_embeds
                SET last_used_at = NOW()
                WHERE embed_id = :embed_id
                """
            ),
            {"embed_id": row.embed_id},
        )

        return {
            "embed_id": row.embed_id,
            "external_user": row.external_user,
            "user_attrs": row.user_attrs,
            "expires_at": row.expires_at,
            "request_count": (row.request_count or 0) + 1,
        }


def revoke_session(session_token: str) -> bool:
    """Mark a session revoked. Returns True if a row was updated."""
    if not session_token:
        return False
    eng = _get_engine()
    with eng.begin() as conn:
        result = conn.execute(
            text(
                """
                UPDATE public.dash_embed_sessions
                SET revoked = TRUE
                WHERE session_token = :tok AND revoked = FALSE
                """
            ),
            {"tok": session_token},
        )
        return (result.rowcount or 0) > 0
