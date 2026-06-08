"""Embed CRUD manager — Phase 1.

DB-backed create/list/get/update/delete + secret rotation for `dash_agent_embeds`.

Plaintext secret_key is returned ONCE on create or rotate; only the hash is
ever persisted.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import text

from dash.embed import (
    _get_engine,
    gen_embed_id,
    gen_public_key,
    gen_secret_key,
    hash_secret,
)
from dash.embed.secret_storage import encrypt_secret

logger = logging.getLogger(__name__)


# Fields the caller is allowed to update via update_embed().
_UPDATABLE_FIELDS = {
    "name",
    "allowed_origins",
    "user_id_required",
    "user_id_signed",
    "auth_mode",
    "jwt_jwks_url",
    "rate_limit_per_min",
    "feature_config",
    "enabled",
    # Visibility policy binding (Phase: embed→policy wiring)
    "bound_scope_id",
    "bound_intent",
    "bound_role",
    # Per-agent theme (migration 062)
    "primary_color",
    "logo_url",
    "welcome_msg",
    "position",
    "theme",
    "faq_mode",
    "status",
    # Consumer mode + sandbox access (migration 063)
    "response_style",
    "access_mode",
    "test_ip_allowlist",
    "max_reply_chars",
    # Row-level security (migration 064)
    "rls_enabled",
    "rls_claims",
    "rls_policies",
    "rls_claim_source",
}

_VALID_AUTH_MODES = {"public", "hmac", "jwt"}
_VALID_INTENTS = {"private", "network", "public"}


def _row_to_dict(row, *, include_hash: bool = False) -> dict[str, Any]:
    """Convert a SQLAlchemy Row mapping into a plain dict.

    Strips secret_key_hash unless include_hash is True. Coerces JSONB
    feature_config to a dict if it arrived as a string.
    """
    if row is None:
        return {}
    d = dict(row._mapping) if hasattr(row, "_mapping") else dict(row)
    if not include_hash:
        d.pop("secret_key_hash", None)
    fc = d.get("feature_config")
    if isinstance(fc, str):
        try:
            d["feature_config"] = json.loads(fc)
        except Exception:
            d["feature_config"] = None
    # Stringify timestamps for JSON friendliness.
    for k in ("created_at", "last_used_at"):
        if d.get(k) is not None:
            d[k] = str(d[k])
    return d


def create_embed(
    project_slug: str,
    name: str | None,
    allowed_origins: list[str] | None,
    user_id_required: bool = False,
    user_id_signed: bool = True,
    auth_mode: str = "hmac",
    jwt_jwks_url: str | None = None,
    rate_limit_per_min: int = 30,
    feature_config: dict | None = None,
    created_by: int | None = None,
    bound_scope_id: str | None = None,
    bound_intent: str | None = None,
    bound_role: str | None = None,
) -> dict[str, Any]:
    """Create a new embed config.

    Returns the full embed dict INCLUDING the plaintext secret_key — caller
    must surface it to the user once and never read it back.
    """
    if auth_mode not in _VALID_AUTH_MODES:
        raise ValueError(f"auth_mode must be one of {_VALID_AUTH_MODES}")
    if bound_intent is not None and bound_intent not in _VALID_INTENTS:
        raise ValueError(f"bound_intent must be one of {_VALID_INTENTS}")
    embed_id = gen_embed_id()
    public_key = gen_public_key()
    secret_key = gen_secret_key()
    secret_hash = hash_secret(secret_key)
    # Encrypt-at-rest for HMAC verification (auth.py reads + decrypts).
    # Plaintext `secret_key` column stays NULL for new rows; legacy rows
    # continue to work via fallback in auth.py.
    secret_enc = encrypt_secret(secret_key)

    eng = _get_engine()
    with eng.connect() as conn:
        conn.execute(
            text(
                """
                INSERT INTO public.dash_agent_embeds
                  (embed_id, project_slug, public_key, secret_key_hash,
                   secret_key_encrypted, name,
                   allowed_origins, user_id_required, user_id_signed,
                   auth_mode, jwt_jwks_url, rate_limit_per_min,
                   feature_config, created_by,
                   bound_scope_id, bound_intent, bound_role)
                VALUES
                  (:embed_id, :slug, :pub, :hash,
                   :enc, :name,
                   :origins, :uid_req, :uid_signed,
                   :auth_mode, :jwks, :rate,
                   CAST(:fc AS JSONB), :created_by,
                   :bsid, :bint, :brole)
                """
            ),
            {
                "embed_id": embed_id,
                "slug": project_slug,
                "pub": public_key,
                "hash": secret_hash,
                "enc": secret_enc,
                "name": name,
                "origins": list(allowed_origins or []),
                "uid_req": bool(user_id_required),
                "uid_signed": bool(user_id_signed),
                "auth_mode": auth_mode,
                "jwks": jwt_jwks_url,
                "rate": int(rate_limit_per_min or 30),
                "fc": json.dumps(feature_config) if feature_config is not None else None,
                "created_by": created_by,
                "bsid": bound_scope_id,
                "bint": bound_intent or "public",
                "brole": bound_role,
            },
        )
        conn.commit()
        row = conn.execute(
            text(
                "SELECT * FROM public.dash_agent_embeds WHERE embed_id = :e"
            ),
            {"e": embed_id},
        ).fetchone()

    out = _row_to_dict(row, include_hash=False)
    out["secret_key"] = secret_key  # plaintext, shown ONCE
    return out


def _live_outlet_codes(conn) -> list[str]:
    """DISTINCT site_code from the latest stock upload — same resolver the
    gateway-key auto-provision uses (auth._live_outlets). Returns [] on any
    failure so the caller stays fail-soft."""
    schema = "citypharma"
    try:
        from dash.tools.table_sync import latest_table_sa, STOCK_COLS
        stock_tbl = latest_table_sa(conn, schema, STOCK_COLS) or "balance_stock_07052026"
        rows = conn.execute(text(
            f'SELECT DISTINCT site_code FROM "{schema}"."{stock_tbl}" '
            "WHERE site_code IS NOT NULL AND site_code <> '' ORDER BY site_code ASC"
        )).fetchall()
        return [r[0] for r in rows if r[0]]
    except Exception:
        logger.exception("embed: _live_outlet_codes failed")
        return []


def auto_provision_store_embeds(project_slug: str) -> int:
    """Create one public store-bound embed (name `store-<site_code>`) for every
    live outlet that has none yet — the embed twin of auth._auto_provision_missing.

    Production widget shape: auth_mode=public, bound_role=staff, consumer/masked
    (column defaults: response_style=consumer, bound_intent=public, status=live,
    enabled=true, auto_provisioned=false). Idempotent on the `store-<code>` name.

    Gated by env EMBED_AUTO_PROVISION (default '1' = on). Fully fail-soft.
    Returns the count of embeds created this call (0 if gate off / nothing to do).
    """
    import os
    if os.getenv("EMBED_AUTO_PROVISION", "1") != "1":
        return 0
    try:
        eng = _get_engine()
        with eng.connect() as conn:
            outlets = _live_outlet_codes(conn)
            if not outlets:
                return 0
            # Existing store embeds for this project, keyed by bound outlet code.
            rows = conn.execute(text(
                "SELECT name, bound_scope_id FROM public.dash_agent_embeds "
                "WHERE project_slug = :s AND name LIKE 'store-%'"
            ), {"s": project_slug}).fetchall()
            have: set[str] = set()
            for nm, bsid in rows:
                if bsid:
                    have.add(bsid)
                elif nm and nm.startswith("store-"):
                    have.add(nm[len("store-"):])
            missing = [o for o in outlets if o not in have]
            if not missing:
                return 0
            # Resolve owner user_id for created_by (INTEGER column).
            owner = conn.execute(text(
                "SELECT user_id FROM public.dash_projects WHERE slug = :s LIMIT 1"
            ), {"s": project_slug}).fetchone()
            created_by = int(owner[0]) if owner and owner[0] is not None else None
    except Exception:
        logger.exception("embed: auto_provision_store_embeds setup failed")
        return 0

    made = 0
    for code in missing:
        try:
            create_embed(
                project_slug=project_slug,
                name=f"store-{code}",
                allowed_origins=[],          # any origin, like the existing 53
                auth_mode="public",
                rate_limit_per_min=120,
                created_by=created_by,
                bound_scope_id=code,
                bound_intent="public",       # masked (consumer drop-in)
                bound_role="staff",
            )
            made += 1
        except Exception:
            logger.exception("embed: auto-provision failed for outlet %s", code)
    if made:
        logger.info("embed: auto-provisioned %d new store embeds", made)
    return made


def list_embeds(project_slug: str) -> list[dict[str, Any]]:
    eng = _get_engine()
    with eng.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT * FROM public.dash_agent_embeds
                WHERE project_slug = :s
                ORDER BY created_at DESC
                """
            ),
            {"s": project_slug},
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_embed(embed_id: str) -> dict[str, Any] | None:
    eng = _get_engine()
    with eng.connect() as conn:
        row = conn.execute(
            text("SELECT * FROM public.dash_agent_embeds WHERE embed_id = :e"),
            {"e": embed_id},
        ).fetchone()
    if not row:
        return None
    return _row_to_dict(row)


def update_embed(embed_id: str, **fields: Any) -> dict[str, Any]:
    """Update mutable fields on an embed. secret_key cannot be changed here."""
    updates: dict[str, Any] = {}
    for k, v in fields.items():
        if k not in _UPDATABLE_FIELDS or v is None:
            continue
        updates[k] = v

    if "auth_mode" in updates and updates["auth_mode"] not in _VALID_AUTH_MODES:
        raise ValueError(f"auth_mode must be one of {_VALID_AUTH_MODES}")
    if "bound_intent" in updates and updates["bound_intent"] not in _VALID_INTENTS:
        raise ValueError(f"bound_intent must be one of {_VALID_INTENTS}")

    if not updates:
        existing = get_embed(embed_id)
        if existing is None:
            raise ValueError("embed not found")
        return existing

    set_parts: list[str] = []
    params: dict[str, Any] = {"embed_id": embed_id}
    for k, v in updates.items():
        if k in ("feature_config", "rls_claims", "rls_policies"):
            set_parts.append(f"{k} = CAST(:{k} AS JSONB)")
            params[k] = json.dumps(v) if v is not None else None
        elif k == "allowed_origins":
            set_parts.append(f"{k} = :{k}")
            params[k] = list(v or [])
        else:
            set_parts.append(f"{k} = :{k}")
            params[k] = v

    sql = (
        "UPDATE public.dash_agent_embeds SET "
        + ", ".join(set_parts)
        + " WHERE embed_id = :embed_id"
    )

    eng = _get_engine()
    with eng.connect() as conn:
        result = conn.execute(text(sql), params)
        conn.commit()
        if result.rowcount == 0:
            raise ValueError("embed not found")
        row = conn.execute(
            text("SELECT * FROM public.dash_agent_embeds WHERE embed_id = :e"),
            {"e": embed_id},
        ).fetchone()
    return _row_to_dict(row)


def delete_embed(embed_id: str) -> bool:
    eng = _get_engine()
    with eng.connect() as conn:
        res = conn.execute(
            text("DELETE FROM public.dash_agent_embeds WHERE embed_id = :e"),
            {"e": embed_id},
        )
        conn.commit()
    return res.rowcount > 0


def rotate_public_key(embed_id: str) -> str:
    """Generate a new public_key for the embed (invalidates the old one).

    For public-auth embeds the public_key IS the credential, so this is the
    'rotate key' action. Any site still using the old key stops working until
    its snippet is updated. Returns the new public_key.
    """
    public_key = gen_public_key()
    eng = _get_engine()
    with eng.connect() as conn:
        res = conn.execute(
            text(
                "UPDATE public.dash_agent_embeds SET public_key = :pk "
                "WHERE embed_id = :e"
            ),
            {"pk": public_key, "e": embed_id},
        )
        conn.commit()
        if res.rowcount == 0:
            raise ValueError("embed not found")
    return public_key


def rotate_secret(embed_id: str) -> str:
    """Generate a new secret_key, persist hash + encrypted ciphertext, return plaintext once.

    Also clears the legacy plaintext `secret_key` column so post-rotation the
    encrypted column is the sole source of truth.
    """
    secret_key = gen_secret_key()
    secret_hash = hash_secret(secret_key)
    secret_enc = encrypt_secret(secret_key)
    eng = _get_engine()
    with eng.connect() as conn:
        res = conn.execute(
            text(
                "UPDATE public.dash_agent_embeds SET "
                "  secret_key_hash = :h, "
                "  secret_key_encrypted = :enc, "
                "  secret_key = NULL "
                "WHERE embed_id = :e"
            ),
            {"h": secret_hash, "enc": secret_enc, "e": embed_id},
        )
        conn.commit()
        if res.rowcount == 0:
            raise ValueError("embed not found")
    return secret_key
