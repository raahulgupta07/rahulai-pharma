"""LLM key registry — encrypted CRUD over dash.dash_llm_keys.

Reuses Fernet from dash.connectors.crypto (CONNECTION_ENCRYPTION_KEY env).
Read path called by dash.llm_client.OpenRouterPool on 60s refresh.
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import text

from dash.connectors.crypto import get_fernet

logger = logging.getLogger(__name__)


def _engine():
    from db.session import get_write_engine
    return get_write_engine()


def _encrypt(plaintext: str) -> str:
    return get_fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def _decrypt(token: str) -> str:
    return get_fernet().decrypt(token.encode("ascii")).decode("utf-8")


def add_key(*, label: str, raw_key: str, created_by: Optional[int] = None, notes: Optional[str] = None) -> dict:
    """Insert new encrypted key. Returns row dict (without plaintext)."""
    raw_key = (raw_key or "").strip()
    if not raw_key:
        raise ValueError("empty key")
    suffix = raw_key[-6:]
    enc = _encrypt(raw_key)
    with _engine().begin() as conn:
        row = conn.execute(text("""
            INSERT INTO dash.dash_llm_keys (key_label, encrypted_key, key_suffix, created_by, notes)
            VALUES (:label, :enc, :sfx, :by, :notes)
            RETURNING id, key_label, key_suffix, enabled, created_at
        """), {"label": label, "enc": enc, "sfx": suffix, "by": created_by, "notes": notes}).mappings().first()
    return dict(row) if row else {}


def list_keys(*, include_disabled: bool = True) -> list[dict]:
    """List keys (no plaintext, only suffix)."""
    q = "SELECT id, key_label, key_suffix, provider, enabled, created_at, last_used_at, notes FROM dash.dash_llm_keys"
    if not include_disabled:
        q += " WHERE enabled = TRUE"
    q += " ORDER BY created_at DESC"
    with _engine().connect() as conn:
        return [dict(r) for r in conn.execute(text(q)).mappings().all()]


def set_enabled(key_id: int, enabled: bool) -> bool:
    with _engine().begin() as conn:
        r = conn.execute(text("UPDATE dash.dash_llm_keys SET enabled=:e WHERE id=:i"),
                         {"e": enabled, "i": key_id})
    return r.rowcount > 0


def delete_key(key_id: int) -> bool:
    with _engine().begin() as conn:
        r = conn.execute(text("DELETE FROM dash.dash_llm_keys WHERE id=:i"), {"i": key_id})
    return r.rowcount > 0


def load_active_plaintext_keys() -> list[str]:
    """Pool read path. Returns decrypted plaintext keys for enabled rows.
    Fail-soft: bad ciphertext rows are skipped + logged."""
    out: list[str] = []
    try:
        with _engine().connect() as conn:
            rows = conn.execute(text(
                "SELECT id, encrypted_key FROM dash.dash_llm_keys WHERE enabled=TRUE AND provider='openrouter'"
            )).mappings().all()
    except Exception as e:
        logger.warning("load_active_plaintext_keys: DB read failed: %s", e)
        return []
    for r in rows:
        try:
            out.append(_decrypt(r["encrypted_key"]))
        except Exception as e:
            logger.warning("load_active_plaintext_keys: decrypt failed for id=%s: %s", r["id"], e)
    return out


def update_key(
    key_id: int,
    *,
    label: Optional[str] = None,
    notes: Optional[str] = None,
    enabled: Optional[bool] = None,
    raw_key: Optional[str] = None,
) -> Optional[dict]:
    """Dynamic UPDATE on dash.dash_llm_keys.

    - If raw_key is provided: re-encrypts via Fernet, updates encrypted_key +
      key_suffix, and resets last_used_at = NULL.
    - Returns updated row dict (no plaintext) or None if not found.
    """
    sets: list[str] = []
    params: dict = {"id": key_id}

    if label is not None:
        sets.append("key_label = :label")
        params["label"] = label
    if notes is not None:
        sets.append("notes = :notes")
        params["notes"] = notes
    if enabled is not None:
        sets.append("enabled = :enabled")
        params["enabled"] = bool(enabled)
    if raw_key is not None:
        rk = (raw_key or "").strip()
        if not rk:
            raise ValueError("raw_key empty")
        sets.append("encrypted_key = :enc")
        sets.append("key_suffix = :sfx")
        sets.append("last_used_at = NULL")
        params["enc"] = _encrypt(rk)
        params["sfx"] = rk[-6:]

    if not sets:
        # nothing to update — fetch + return current row
        with _engine().connect() as conn:
            row = conn.execute(text(
                "SELECT id, key_label, key_suffix, provider, enabled, created_at, last_used_at, notes "
                "FROM dash.dash_llm_keys WHERE id=:id"
            ), {"id": key_id}).mappings().first()
        return dict(row) if row else None

    sql = (
        "UPDATE dash.dash_llm_keys SET " + ", ".join(sets) +
        " WHERE id = :id "
        "RETURNING id, key_label, key_suffix, provider, enabled, created_at, last_used_at, notes"
    )
    with _engine().begin() as conn:
        row = conn.execute(text(sql), params).mappings().first()
    return dict(row) if row else None


def mark_used(key_suffix: str):
    """Optional: stamp last_used_at after successful call. Best-effort, async-safe."""
    try:
        with _engine().begin() as conn:
            conn.execute(text(
                "UPDATE dash.dash_llm_keys SET last_used_at=now() WHERE key_suffix=:s AND enabled=TRUE"
            ), {"s": key_suffix})
    except Exception:
        pass
