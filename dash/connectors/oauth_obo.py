"""OAuth On-Behalf-Of (OBO) helpers for true per-user PowerBI RLS.

Stores Fernet-encrypted per-user refresh + access tokens in
`dash.dash_connection_user_tokens` (migration 116). When PowerBI is configured
with `auth_mode='obo'`, every API call uses the calling user's token instead
of the service principal token — PowerBI then applies the user's row-level
security automatically.

Soft-imports MSAL (already in requirements per SharePoint connector).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text

logger = logging.getLogger(__name__)

try:
    import msal as _msal  # type: ignore
    _HAVE_MSAL = True
except ImportError:
    _HAVE_MSAL = False


class OBOTokenMissingError(Exception):
    """Raised when no stored OBO token exists for (connection_id, user_id).

    Caller should redirect the user to the consent URL
    (GET /api/connections/{id}/obo/consent-url) to authorize the SP to act
    on their behalf.
    """


class OBOTokenError(Exception):
    """Raised on MSAL token exchange / refresh failure."""


def _require_msal() -> None:
    if not _HAVE_MSAL:
        raise RuntimeError(
            "OBO flow requires msal. Add msal to requirements.txt."
        )


def _authority(tenant_id: str) -> str:
    return f"https://login.microsoftonline.com/{tenant_id}"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _expires_at_from(expires_in: int | None) -> datetime:
    secs = int(expires_in or 3600)
    return _now_utc() + timedelta(seconds=max(60, secs - 60))  # 60s safety margin


def exchange_for_obo_token(
    tenant_id: str,
    client_id: str,
    client_secret: str,
    user_assertion: str,
    scope: str,
) -> dict:
    """Exchange a user's access-token assertion for an OBO token.

    Uses MSAL ConfidentialClientApplication.acquire_token_on_behalf_of.
    Returns dict with access_token, refresh_token, expires_in, expires_at (UTC ISO).
    """
    _require_msal()
    app = _msal.ConfidentialClientApplication(
        client_id=client_id,
        client_credential=client_secret,
        authority=_authority(tenant_id),
    )
    result = app.acquire_token_on_behalf_of(
        user_assertion=user_assertion,
        scopes=[scope],
    )
    if not result or "access_token" not in result:
        raise OBOTokenError(
            f"OBO exchange failed: {result.get('error_description') if result else 'no response'}"
        )
    expires_at = _expires_at_from(result.get("expires_in"))
    return {
        "access_token": result["access_token"],
        "refresh_token": result.get("refresh_token"),
        "expires_in": int(result.get("expires_in") or 3600),
        "expires_at": expires_at.isoformat(),
    }


def refresh_obo_token(
    tenant_id: str,
    client_id: str,
    client_secret: str,
    refresh_token: str,
    scope: str,
) -> dict:
    """Refresh an OBO token using a stored refresh token."""
    _require_msal()
    app = _msal.ConfidentialClientApplication(
        client_id=client_id,
        client_credential=client_secret,
        authority=_authority(tenant_id),
    )
    result = app.acquire_token_by_refresh_token(
        refresh_token=refresh_token,
        scopes=[scope],
    )
    if not result or "access_token" not in result:
        raise OBOTokenError(
            f"OBO refresh failed: {result.get('error_description') if result else 'no response'}"
        )
    expires_at = _expires_at_from(result.get("expires_in"))
    return {
        "access_token": result["access_token"],
        "refresh_token": result.get("refresh_token") or refresh_token,
        "expires_in": int(result.get("expires_in") or 3600),
        "expires_at": expires_at.isoformat(),
    }


def acquire_token_by_authorization_code(
    tenant_id: str,
    client_id: str,
    client_secret: str,
    code: str,
    scope: str,
    redirect_uri: str,
) -> dict:
    """Exchange an auth-code (consent callback) for access + refresh tokens."""
    _require_msal()
    app = _msal.ConfidentialClientApplication(
        client_id=client_id,
        client_credential=client_secret,
        authority=_authority(tenant_id),
    )
    result = app.acquire_token_by_authorization_code(
        code=code,
        scopes=[scope],
        redirect_uri=redirect_uri,
    )
    if not result or "access_token" not in result:
        raise OBOTokenError(
            f"auth-code exchange failed: {result.get('error_description') if result else 'no response'}"
        )
    expires_at = _expires_at_from(result.get("expires_in"))
    return {
        "access_token": result["access_token"],
        "refresh_token": result.get("refresh_token"),
        "expires_in": int(result.get("expires_in") or 3600),
        "expires_at": expires_at.isoformat(),
    }


def build_consent_url(
    tenant_id: str,
    client_id: str,
    client_secret: str,
    scope: str,
    redirect_uri: str,
    state: str,
) -> str:
    """Build an Azure AD authorization-request URL for OBO consent."""
    _require_msal()
    app = _msal.ConfidentialClientApplication(
        client_id=client_id,
        client_credential=client_secret,
        authority=_authority(tenant_id),
    )
    return app.get_authorization_request_url(
        scopes=[scope],
        state=state,
        redirect_uri=redirect_uri,
    )


# ---------------------------------------------------------------------------
# Storage layer — dash.dash_connection_user_tokens
# ---------------------------------------------------------------------------
def _get_engine():
    from db.session import get_write_engine

    return get_write_engine()


def _parse_expires(val: Any) -> datetime | None:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val if val.tzinfo else val.replace(tzinfo=timezone.utc)
    if isinstance(val, str):
        try:
            dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:
            return None
    return None


def store_user_token(
    connection_id: str,
    user_id: int,
    token_data: dict,
) -> None:
    """Persist Fernet-encrypted access + refresh tokens for (conn, user)."""
    from dash.connectors.crypto import encrypt_credentials

    enc_refresh = encrypt_credentials({"t": token_data.get("refresh_token") or ""})
    enc_access = encrypt_credentials({"t": token_data.get("access_token") or ""})
    expires_at = _parse_expires(token_data.get("expires_at"))

    eng = _get_engine()
    with eng.begin() as c:
        c.execute(
            text(
                """
                INSERT INTO dash.dash_connection_user_tokens
                    (connection_id, user_id, refresh_token, access_token, expires_at, updated_at)
                VALUES
                    (:cid, :uid, :rt, :at, :exp, now())
                ON CONFLICT (connection_id, user_id) DO UPDATE SET
                    refresh_token = EXCLUDED.refresh_token,
                    access_token = EXCLUDED.access_token,
                    expires_at = EXCLUDED.expires_at,
                    updated_at = now()
                """
            ),
            {
                "cid": str(connection_id),
                "uid": int(user_id),
                "rt": enc_refresh,
                "at": enc_access,
                "exp": expires_at,
            },
        )


def delete_user_token(connection_id: str, user_id: int) -> int:
    eng = _get_engine()
    with eng.begin() as c:
        res = c.execute(
            text(
                """
                DELETE FROM dash.dash_connection_user_tokens
                WHERE connection_id = :cid AND user_id = :uid
                """
            ),
            {"cid": str(connection_id), "uid": int(user_id)},
        )
        return res.rowcount or 0


def get_token_status(connection_id: str, user_id: int) -> dict:
    eng = _get_engine()
    with eng.connect() as c:
        row = c.execute(
            text(
                """
                SELECT expires_at FROM dash.dash_connection_user_tokens
                WHERE connection_id = :cid AND user_id = :uid
                """
            ),
            {"cid": str(connection_id), "uid": int(user_id)},
        ).fetchone()
    if not row:
        return {"has_token": False, "expires_at": None}
    exp = _parse_expires(row[0])
    return {
        "has_token": True,
        "expires_at": exp.isoformat() if exp else None,
    }


def _load_stored_tokens(connection_id: str, user_id: int) -> dict | None:
    from dash.connectors.crypto import decrypt_credentials

    eng = _get_engine()
    with eng.connect() as c:
        row = c.execute(
            text(
                """
                SELECT refresh_token, access_token, expires_at
                FROM dash.dash_connection_user_tokens
                WHERE connection_id = :cid AND user_id = :uid
                """
            ),
            {"cid": str(connection_id), "uid": int(user_id)},
        ).fetchone()
    if not row:
        return None
    rt_enc, at_enc, exp = row
    try:
        refresh_token = decrypt_credentials(rt_enc).get("t") if rt_enc else None
    except Exception:
        refresh_token = None
    try:
        access_token = decrypt_credentials(at_enc).get("t") if at_enc else None
    except Exception:
        access_token = None
    return {
        "refresh_token": refresh_token,
        "access_token": access_token,
        "expires_at": _parse_expires(exp),
    }


def get_user_obo_token(
    connection_id: str,
    user_id: int | None,
    conn_creds: dict,
    scope: str = "https://analysis.windows.net/powerbi/api/.default",
) -> str:
    """Main entry — returns a valid access_token for the user, refreshing if needed.

    conn_creds must contain tenant_id, client_id, client_secret.
    Raises OBOTokenMissingError if no token stored (UI should redirect to consent).
    """
    if user_id is None:
        raise OBOTokenMissingError("no user_id in context for OBO call")

    stored = _load_stored_tokens(connection_id, user_id)
    if not stored:
        raise OBOTokenMissingError(
            f"no OBO token for connection_id={connection_id} user_id={user_id} — consent required"
        )

    # Token still valid?
    now = _now_utc()
    if stored.get("access_token") and stored.get("expires_at"):
        if stored["expires_at"] > now + timedelta(seconds=30):
            return stored["access_token"]

    # Need refresh
    if not stored.get("refresh_token"):
        raise OBOTokenMissingError(
            f"OBO access_token expired and no refresh_token for user_id={user_id} — re-consent required"
        )

    refreshed = refresh_obo_token(
        tenant_id=conn_creds["tenant_id"],
        client_id=conn_creds["client_id"],
        client_secret=conn_creds["client_secret"],
        refresh_token=stored["refresh_token"],
        scope=scope,
    )
    store_user_token(connection_id, user_id, refreshed)
    return refreshed["access_token"]


__all__ = [
    "OBOTokenMissingError",
    "OBOTokenError",
    "exchange_for_obo_token",
    "refresh_obo_token",
    "acquire_token_by_authorization_code",
    "build_consent_url",
    "store_user_token",
    "delete_user_token",
    "get_token_status",
    "get_user_obo_token",
]
