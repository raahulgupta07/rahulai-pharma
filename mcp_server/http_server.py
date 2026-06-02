"""HTTP transport for Dash MCP.

Exposes the same JSON-RPC 2.0 envelope as the stdio bridge under
``POST /api/mcp/rpc`` so that hosted clients (ChatGPT custom GPTs,
Claude Desktop's HTTP MCP backend, n8n, etc.) can speak to Dash without
shelling out a stdio subprocess.

Also exposes admin endpoints (``/api/admin/mcp/tokens/*``) for minting
and revoking per-user MCP tokens — these are separate from the regular
Dash session tokens so a leaked MCP token doesn't unlock the web UI.

Bearer auth: ``Authorization: Bearer <dash_mcp_token>``. Token format
is ``dash-mcp-<32 url-safe hex>`` and lives in
``public.dash_mcp_tokens`` (created on first use).
"""

from __future__ import annotations

import logging
import secrets
import time
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from .auth import verify_token
from .main import (
    PROTOCOL_VERSION,
    SERVER_NAME,
    SERVER_VERSION,
    _dispatch,
)
from .tools_registry import list_tools

log = logging.getLogger("mcp.http")

# ---------------------------------------------------------------------------
# Engine + table bootstrap
# ---------------------------------------------------------------------------

try:
    from db import db_url  # type: ignore
except Exception:  # pragma: no cover - import-time guard
    db_url = None  # the router still imports; calls just 503.


def _engine():
    if not db_url:
        raise HTTPException(503, "db not configured")
    return create_engine(db_url, poolclass=NullPool)


_BOOTSTRAPPED = False


def _bootstrap() -> None:
    global _BOOTSTRAPPED
    if _BOOTSTRAPPED or not db_url:
        return
    eng = _engine()
    try:
        with eng.connect() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS public.dash_mcp_tokens (
                    id          SERIAL PRIMARY KEY,
                    token       TEXT UNIQUE NOT NULL,
                    user_id     INTEGER NOT NULL REFERENCES public.dash_users(id) ON DELETE CASCADE,
                    username    TEXT NOT NULL,
                    name        TEXT,
                    scopes      TEXT[] DEFAULT '{}',
                    last_used_at TIMESTAMP,
                    expires_at  TIMESTAMP,
                    revoked_at  TIMESTAMP,
                    created_at  TIMESTAMP DEFAULT NOW()
                )
            """))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_mcp_tokens_user "
                "ON public.dash_mcp_tokens(user_id)"
            ))
            conn.commit()
    finally:
        eng.dispose()
    _BOOTSTRAPPED = True


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def _resolve_mcp_token(raw: str | None) -> dict[str, Any] | None:
    """An MCP token is either:

    1. A ``dash-mcp-…`` token from ``dash_mcp_tokens``, or
    2. A regular Dash session/API token (so the web UI's API key works
       seamlessly with MCP HTTP).
    """
    if not raw:
        return None
    _bootstrap()

    if raw.startswith("dash-mcp-"):
        eng = _engine()
        try:
            with eng.connect() as conn:
                row = conn.execute(text(
                    "SELECT user_id, username, expires_at, revoked_at "
                    "FROM public.dash_mcp_tokens WHERE token = :t"
                ), {"t": raw}).fetchone()
                if not row or row[3] is not None:
                    return None
                if row[2] is not None and row[2].timestamp() < time.time():
                    return None
                conn.execute(text(
                    "UPDATE public.dash_mcp_tokens SET last_used_at = NOW() "
                    "WHERE token = :t"
                ), {"t": raw})
                conn.commit()
                import os as _os

                return {
                    "user_id": row[0],
                    "username": row[1],
                    "expiry": float(row[2].timestamp()) if row[2] else float("inf"),
                    "is_super": row[1] == _os.getenv("SUPER_ADMIN", "admin"),
                    "mcp_token": True,
                }
        finally:
            eng.dispose()

    return verify_token(raw)


def _auth_required(authorization: str | None = Header(None)) -> dict[str, Any]:
    token = ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(None, 1)[1].strip()
    user = _resolve_mcp_token(token)
    if not user:
        raise HTTPException(401, "invalid or missing MCP bearer token")
    return user


def _session_user_required(request: Request) -> dict[str, Any]:
    """For admin endpoints, expect a normal Dash session token so the
    user can mint MCP tokens from the web UI."""
    try:
        from app.auth import get_current_user  # type: ignore
    except Exception:
        raise HTTPException(503, "app.auth unavailable")
    u = get_current_user(request)
    if not u:
        raise HTTPException(401, "not authenticated")
    return u


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/api/mcp", tags=["MCP"])
admin_router = APIRouter(prefix="/api/admin/mcp", tags=["MCP Admin"])


@router.get("/info")
def info() -> dict:
    """Public-facing capability info — no auth required."""
    return {
        "name": SERVER_NAME,
        "version": SERVER_VERSION,
        "protocolVersion": PROTOCOL_VERSION,
        "transport": "http",
        "rpc_endpoint": "/api/mcp/rpc",
        "tools": [t["name"] for t in list_tools()],
    }


@router.post("/rpc")
async def rpc(request: Request, user: dict = Depends(_auth_required)) -> dict:
    """Single JSON-RPC entry point (batches not supported in v1)."""
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(400, "invalid JSON body")

    if isinstance(payload, list):
        # Batch
        return {"batch": [_dispatch(m, user) for m in payload]}

    response = _dispatch(payload, user)
    if response is None:  # notification
        return {"status": "accepted"}
    return response


# ---------------------------------------------------------------------------
# Admin: mint / list / revoke MCP tokens (current user)
# ---------------------------------------------------------------------------

class MintTokenRequest(BaseModel):
    name: str | None = None
    ttl_days: int | None = None  # None = no expiry
    scopes: list[str] | None = None


@admin_router.post("/tokens")
def mint_token(req: MintTokenRequest, user: dict = Depends(_session_user_required)) -> dict:
    """Mint a new MCP token for the current user."""
    _bootstrap()
    token = f"dash-mcp-{secrets.token_urlsafe(24)}"
    expires = None
    if req.ttl_days and req.ttl_days > 0:
        expires = time.time() + (req.ttl_days * 86400)

    eng = _engine()
    try:
        with eng.connect() as conn:
            conn.execute(text("""
                INSERT INTO public.dash_mcp_tokens
                    (token, user_id, username, name, scopes, expires_at)
                VALUES (:t, :uid, :u, :n, :sc,
                        CASE WHEN :exp IS NULL THEN NULL
                             ELSE to_timestamp(:exp) END)
            """), {
                "t": token,
                "uid": user["user_id"],
                "u": user["username"],
                "n": req.name or "mcp",
                "sc": req.scopes or [],
                "exp": expires,
            })
            conn.commit()
    finally:
        eng.dispose()

    return {
        "token": token,
        "name": req.name,
        "scopes": req.scopes or [],
        "expires_at": expires,
        "warning": "Store this token now — it will not be shown again.",
    }


@admin_router.get("/tokens")
def list_tokens(user: dict = Depends(_session_user_required)) -> dict:
    _bootstrap()
    eng = _engine()
    try:
        with eng.connect() as conn:
            rows = conn.execute(text(
                "SELECT id, name, scopes, last_used_at, expires_at, "
                "revoked_at, created_at "
                "FROM public.dash_mcp_tokens "
                "WHERE user_id = :uid AND revoked_at IS NULL "
                "ORDER BY created_at DESC"
            ), {"uid": user["user_id"]}).fetchall()
    finally:
        eng.dispose()
    return {
        "tokens": [
            {
                "id": r[0], "name": r[1], "scopes": r[2] or [],
                "last_used_at": str(r[3]) if r[3] else None,
                "expires_at": str(r[4]) if r[4] else None,
                "revoked_at": str(r[5]) if r[5] else None,
                "created_at": str(r[6]) if r[6] else None,
            }
            for r in rows
        ],
    }


@admin_router.delete("/tokens/{token_id}")
def revoke_token(token_id: int, user: dict = Depends(_session_user_required)) -> dict:
    _bootstrap()
    eng = _engine()
    try:
        with eng.connect() as conn:
            res = conn.execute(text(
                "UPDATE public.dash_mcp_tokens SET revoked_at = NOW() "
                "WHERE id = :id AND user_id = :uid AND revoked_at IS NULL"
            ), {"id": token_id, "uid": user["user_id"]})
            conn.commit()
            if res.rowcount == 0:
                raise HTTPException(404, "token not found")
    finally:
        eng.dispose()
    return {"status": "revoked", "id": token_id}
