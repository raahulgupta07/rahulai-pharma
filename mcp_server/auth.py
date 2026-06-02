"""MCP authentication.

Resolves a Bearer token (or API key) presented by an MCP client into a
Dash user record. Tokens live in ``public.dash_tokens`` (issued by
``/api/auth/login``) or ``public.dash_users.api_key``.

Two convenience shims exist on top of the existing ``app.auth`` module
so that ``mcp_server`` doesn't reach into Dash internals directly when
running under K8s pods where ``app/`` may not be importable (e.g. the
stdio bridge process). Falls back to a raw SQL lookup when ``app.auth``
isn't available.
"""

from __future__ import annotations

import logging
import os
from typing import Any

log = logging.getLogger("mcp.auth")


def _resolve_via_app_auth(token: str) -> dict[str, Any] | None:
    """Use ``app.auth.validate_token`` / ``_validate_api_key`` if the
    full Dash package is importable (in-process / HTTP MCP)."""
    try:
        from app.auth import _validate_api_key, validate_token  # type: ignore
    except Exception:
        return None

    if token.startswith("dash-key-"):
        return _validate_api_key(token)
    return validate_token(token)


def _resolve_via_sql(token: str) -> dict[str, Any] | None:
    """Last-resort path: query ``dash_tokens`` directly. Used by the
    stdio bridge when ``app/`` isn't on sys.path."""
    try:
        import time

        from sqlalchemy import create_engine, text
        from sqlalchemy.pool import NullPool

        from db import db_url  # type: ignore
    except Exception as e:  # pragma: no cover - dev path
        log.debug("mcp.auth.sql_fallback unavailable: %s", e)
        return None

    eng = create_engine(db_url, poolclass=NullPool)
    try:
        with eng.connect() as conn:
            if token.startswith("dash-key-"):
                row = conn.execute(
                    text(
                        "SELECT id, username FROM public.dash_users WHERE api_key = :k"
                    ),
                    {"k": token},
                ).fetchone()
                if not row:
                    return None
                return {
                    "user_id": row[0],
                    "username": row[1],
                    "expiry": float("inf"),
                    "is_super": row[1] == os.getenv("SUPER_ADMIN", "admin"),
                }

            row = conn.execute(
                text(
                    "SELECT user_id, username, expiry FROM public.dash_tokens "
                    "WHERE token = :t"
                ),
                {"t": token},
            ).fetchone()
            if not row or row[2] <= time.time():
                return None
            return {
                "user_id": row[0],
                "username": row[1],
                "expiry": row[2],
                "is_super": row[1] == os.getenv("SUPER_ADMIN", "admin"),
            }
    finally:
        eng.dispose()


def verify_token(token: str | None) -> dict[str, Any] | None:
    """Return the Dash user dict for a token, or ``None`` if invalid.

    The MCP stdio bridge reads its token from the ``DASH_MCP_USER_TOKEN``
    env var; HTTP transport reads it from the ``Authorization`` header.
    """
    if not token:
        token = os.getenv("DASH_MCP_USER_TOKEN", "").strip() or None
    if not token:
        return None

    user = _resolve_via_app_auth(token)
    if user:
        return user
    return _resolve_via_sql(token)


def can_access_project(user: dict[str, Any], slug: str, required: str = "viewer") -> bool:
    """Defer to ``app.auth.check_project_permission`` if available;
    otherwise allow super-admin only."""
    try:
        from app.auth import check_project_permission  # type: ignore

        return bool(check_project_permission(user, slug, required))
    except Exception:
        return bool(user.get("is_super"))
