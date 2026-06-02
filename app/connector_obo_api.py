"""OAuth On-Behalf-Of (OBO) consent + token API for PowerBI per-user RLS.

Endpoints:
  GET    /api/connections/{id}/obo/consent-url   — Azure AD consent URL for SP-as-user
  GET    /api/connections/obo/callback           — OAuth callback, exchanges code → tokens
  DELETE /api/connections/{id}/obo/revoke        — wipe user's stored OBO tokens
  GET    /api/connections/{id}/obo/status        — {has_token, expires_at}

Per Agent J contract. Migration 116 (Agent K) creates
`dash.dash_connection_user_tokens`.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import text

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/connections", tags=["connections-obo"])

_POWERBI_OBO_SCOPE = "https://analysis.windows.net/powerbi/api/.default"


def _get_user(request: Request) -> dict:
    user = getattr(getattr(request, "state", None), "user", None)
    if not user:
        raise HTTPException(401, "Not authenticated")
    return user


def _load_connection(conn_id: str) -> dict:
    from db.session import get_sql_engine

    eng = get_sql_engine()
    with eng.connect() as c:
        row = c.execute(
            text(
                """
                SELECT id, name, connector_type, config, credentials
                FROM dash.dash_connections WHERE id = :i
                """
            ),
            {"i": conn_id},
        ).fetchone()
    if not row:
        raise HTTPException(404, "connection not found")
    d = dict(row._mapping)
    d["id"] = str(d["id"])
    cfg = d.get("config")
    if isinstance(cfg, str):
        try:
            d["config"] = json.loads(cfg)
        except Exception:
            d["config"] = {}
    return d


def _decrypt_creds(row: dict) -> dict:
    from dash.connectors.crypto import decrypt_credentials

    enc = row.get("credentials")
    if not enc:
        raise HTTPException(400, "connection has no stored credentials")
    return decrypt_credentials(enc)


def _redirect_uri(request: Request, conn: dict) -> str:
    """Derive redirect_uri from config or env (must match Azure AD app registration)."""
    cfg = conn.get("config") or {}
    explicit = cfg.get("redirect_uri")
    if explicit:
        return explicit
    env_base = os.environ.get("OBO_REDIRECT_BASE_URL") or os.environ.get("PUBLIC_BASE_URL")
    if env_base:
        return f"{env_base.rstrip('/')}/api/connections/obo/callback"
    # Fall back to request URL
    base = str(request.base_url).rstrip("/")
    return f"{base}/api/connections/obo/callback"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@router.get("/{conn_id}/obo/consent-url")
def get_consent_url(conn_id: str, request: Request):
    user = _get_user(request)
    conn = _load_connection(conn_id)
    if conn["connector_type"] != "powerbi":
        raise HTTPException(400, "OBO consent only supported for powerbi connector")
    creds = _decrypt_creds(conn)

    from dash.connectors.oauth_obo import build_consent_url

    redirect_uri = _redirect_uri(request, conn)
    # State carries connection_id + user_id so callback knows where to store
    state = f"{conn_id}:{user.get('id') or user.get('user_id')}"
    try:
        url = build_consent_url(
            tenant_id=creds["tenant_id"],
            client_id=creds["client_id"],
            client_secret=creds["client_secret"],
            scope=_POWERBI_OBO_SCOPE,
            redirect_uri=redirect_uri,
            state=state,
        )
    except Exception as e:
        logger.exception("build_consent_url failed")
        raise HTTPException(500, f"failed to build consent url: {e}")

    return {"consent_url": url, "redirect_uri": redirect_uri, "state": state}


@router.get("/obo/callback")
def obo_callback(request: Request, code: str | None = None, state: str | None = None, error: str | None = None):
    if error:
        return HTMLResponse(
            f"<html><body><h3>OBO consent error</h3><pre>{error}</pre></body></html>",
            status_code=400,
        )
    if not code or not state:
        raise HTTPException(400, "code and state required")
    if ":" not in state:
        raise HTTPException(400, "invalid state format")

    conn_id, user_id_str = state.split(":", 1)
    try:
        user_id = int(user_id_str)
    except ValueError:
        raise HTTPException(400, "invalid user_id in state")

    # Validate session user matches state user (anti-CSRF)
    sess_user = _get_user(request)
    sess_uid = sess_user.get("id") or sess_user.get("user_id")
    if int(sess_uid) != user_id:
        raise HTTPException(403, "state user does not match session user")

    conn = _load_connection(conn_id)
    if conn["connector_type"] != "powerbi":
        raise HTTPException(400, "OBO callback only for powerbi")
    creds = _decrypt_creds(conn)
    redirect_uri = _redirect_uri(request, conn)

    from dash.connectors.oauth_obo import (
        acquire_token_by_authorization_code,
        store_user_token,
    )

    try:
        token_data = acquire_token_by_authorization_code(
            tenant_id=creds["tenant_id"],
            client_id=creds["client_id"],
            client_secret=creds["client_secret"],
            code=code,
            scope=_POWERBI_OBO_SCOPE,
            redirect_uri=redirect_uri,
        )
        store_user_token(conn_id, user_id, token_data)
    except Exception as e:
        logger.exception("OBO callback token exchange failed")
        return HTMLResponse(
            f"<html><body><h3>Token exchange failed</h3><pre>{e}</pre></body></html>",
            status_code=500,
        )

    return HTMLResponse(
        """
        <html><body style="font-family:sans-serif;padding:40px">
          <h3>✓ Authorization complete</h3>
          <p>You can close this window and return to Dash.</p>
          <script>setTimeout(()=>window.close(), 1500);</script>
        </body></html>
        """
    )


@router.delete("/{conn_id}/obo/revoke")
def revoke_obo(conn_id: str, request: Request):
    user = _get_user(request)
    user_id = int(user.get("id") or user.get("user_id"))
    _load_connection(conn_id)  # 404 if missing

    from dash.connectors.oauth_obo import delete_user_token

    deleted = delete_user_token(conn_id, user_id)
    return {"deleted": deleted}


@router.get("/{conn_id}/obo/status")
def obo_status(conn_id: str, request: Request):
    user = _get_user(request)
    user_id = int(user.get("id") or user.get("user_id"))
    _load_connection(conn_id)

    from dash.connectors.oauth_obo import get_token_status

    return get_token_status(conn_id, user_id)


__all__ = ["router"]
