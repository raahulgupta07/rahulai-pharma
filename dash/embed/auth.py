"""Embed authentication flow — Phase 2.

High-level auth: look up an embed by (embed_id, public_key), verify origin,
verify HMAC user payload (if required), and return the embed context.

SCHEMA NOTE
-----------
Migration 019 stored only `secret_key_hash`. HMAC verification needs the
plaintext secret bytes, so Phase 2 adds `secret_key TEXT` (server-only,
never returned to a browser) via an idempotent ALTER TABLE applied by
`ensure_secret_key_column()` below. Phase 1 (manager.py) is expected to
populate both `secret_key` (plaintext) and `secret_key_hash` going forward.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text

from dash.embed import _get_engine
from dash.embed.session import verify_origin, verify_user_payload

logger = logging.getLogger(__name__)


class EmbedAuthError(Exception):
    """Structured embed authentication failure.

    Carries a machine-readable `code` (lowercase snake_case), HTTP `status`,
    and a human-readable `detail`. Callers should convert into JSON responses
    that include `{detail, code, docs}` for developer debugging.
    """

    def __init__(self, code: str, detail: str, status: int = 403):
        self.code = code
        self.detail = detail
        self.status = status
        super().__init__(detail)


def ensure_secret_key_column() -> None:
    """Idempotent ALTER to add secret_key TEXT column. Safe to call repeatedly."""
    try:
        eng = _get_engine()
        with eng.begin() as conn:
            conn.execute(
                text(
                    "ALTER TABLE public.dash_agent_embeds "
                    "ADD COLUMN IF NOT EXISTS secret_key TEXT"
                )
            )
    except Exception as e:
        logger.warning(f"ensure_secret_key_column failed: {e}")


def ensure_session_claims_column() -> None:
    """Idempotent ALTER to add claims JSONB column on dash_embed_sessions.
    Safe to call repeatedly. Stores extracted RLS claims per session."""
    try:
        eng = _get_engine()
        with eng.begin() as conn:
            conn.execute(
                text(
                    "ALTER TABLE public.dash_embed_sessions "
                    "ADD COLUMN IF NOT EXISTS claims JSONB"
                )
            )
    except Exception as e:
        logger.warning(f"ensure_session_claims_column failed: {e}")


# Run on import so that any caller of this module can rely on the column.
ensure_secret_key_column()
ensure_session_claims_column()


def authenticate_session_request(
    embed_id: str,
    public_key: str,
    user_payload: dict | None,
    signature: str | None,
    origin: str | None,
    ip: str | None,
    server_origin: str | None = None,
) -> dict[str, Any]:
    """Authenticate a session/create request. Raises ValueError on failure.

    Returns:
        {embed_id, project_slug, external_user, user_attrs, feature_config,
         rate_limit_per_min}
    """
    if not embed_id or not public_key:
        raise EmbedAuthError(
            "missing_credentials",
            "embed_id or public_key missing",
            400,
        )

    eng = _get_engine()
    with eng.begin() as conn:
        row = conn.execute(
            text(
                """
                SELECT embed_id, project_slug, public_key, secret_key,
                       secret_key_encrypted, secret_key_hash,
                       allowed_origins, user_id_required, auth_mode, jwt_jwks_url,
                       rate_limit_per_min, feature_config, enabled
                FROM public.dash_agent_embeds
                WHERE embed_id = :embed_id AND public_key = :public_key
                """
            ),
            {"embed_id": embed_id, "public_key": public_key},
        ).fetchone()

    if row is None or not row.enabled:
        raise EmbedAuthError(
            "embed_not_found",
            "embed not found or disabled",
            403,
        )

    allowed_origins = list(row.allowed_origins or [])
    if not verify_origin(allowed_origins, origin, server_origin=server_origin):
        raise EmbedAuthError(
            "origin_denied",
            f"Origin {origin or '(none)'} not in allowlist",
            403,
        )

    auth_mode = (row.auth_mode or "hmac").lower()
    external_user: str | None = None
    user_attrs: dict | None = None

    if auth_mode == "public":
        # Ignore any payload entirely.
        external_user = None
        user_attrs = None

    elif auth_mode == "hmac":
        # Resolve plaintext secret for HMAC verification.
        # Order: (a) decrypt secret_key_encrypted (new rows since migration 161),
        # (b) fall back to row.secret_key plaintext (legacy rows).
        # Only raise "not provisioned" if BOTH are missing.
        secret_plain: str | None = None
        enc = getattr(row, "secret_key_encrypted", None)
        if enc:
            try:
                from dash.embed.secret_storage import decrypt_secret
                secret_plain = decrypt_secret(enc)
            except Exception as e:  # ImproperlyConfigured etc — log + fall through
                logger.warning("embed secret decrypt failed for %s: %s", embed_id, e)
                secret_plain = None
        if not secret_plain:
            secret_plain = row.secret_key  # legacy plaintext fallback
        user_required = bool(row.user_id_required)

        if user_required:
            if not user_payload or not signature:
                raise EmbedAuthError(
                    "payload_required",
                    "auth_mode=hmac with user_id_required needs user payload + signature",
                    400,
                )
            if not secret_plain:
                # Phase 1 hasn't populated plaintext secret yet — cannot verify.
                raise EmbedAuthError(
                    "secret_not_provisioned",
                    "embed has no usable secret; admin must rotate",
                    500,
                )
            if not verify_user_payload(secret_plain, user_payload, signature):
                raise EmbedAuthError(
                    "sig_invalid",
                    "HMAC signature does not match claims",
                    403,
                )
            external_user = str(user_payload.get("id") or user_payload.get("user_id") or "") or None
            user_attrs = {k: v for k, v in user_payload.items() if k not in ("id", "user_id")}
        else:
            # Optional payload — if provided, must verify.
            if user_payload or signature:
                if not user_payload or not signature:
                    raise EmbedAuthError(
                        "partial_auth",
                        "Need both user payload and signature, not one",
                        400,
                    )
                if not secret_plain:
                    raise EmbedAuthError(
                        "secret_not_provisioned",
                        "embed has no usable secret; admin must rotate",
                        500,
                    )
                if not verify_user_payload(secret_plain, user_payload, signature):
                    raise EmbedAuthError(
                        "sig_invalid",
                        "HMAC signature does not match claims",
                        403,
                    )
                external_user = str(user_payload.get("id") or user_payload.get("user_id") or "") or None
                user_attrs = {k: v for k, v in user_payload.items() if k not in ("id", "user_id")}

    elif auth_mode == "jwt":
        raise EmbedAuthError(
            "jwt_unsupported",
            "JWT auth mode not yet implemented; use hmac",
            501,
        )

    else:
        raise EmbedAuthError(
            "unknown_auth_mode",
            f"auth_mode='{auth_mode}' not recognized",
            500,
        )

    return {
        "embed_id": row.embed_id,
        "project_slug": row.project_slug,
        "external_user": external_user,
        "user_attrs": user_attrs,
        "feature_config": row.feature_config,
        "rate_limit_per_min": row.rate_limit_per_min,
    }
