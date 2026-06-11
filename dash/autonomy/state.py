"""Autonomy state persistence + signal diffing — all fail-soft.

Stores the last-seen signal snapshot in `public.dash_autonomy_state` and
appends T3 intents / budget events to `public.dash_autonomy_journal`. Writes go
through `get_write_engine` (the dash read engine carries a public-schema write
guard; this metadata lives in `public`). Nothing here raises — a DB hiccup must
never kill the heartbeat loop.
"""
from __future__ import annotations

import json
import logging

log = logging.getLogger("dash.heartbeat")


def _write_engine():
    from db.session import get_write_engine
    return get_write_engine()


def load_state(slug: str) -> dict:
    """Return the stored signals dict for the project ({} if none / on error)."""
    from sqlalchemy import text
    try:
        eng = _write_engine()
        with eng.connect() as conn:
            row = conn.execute(text(
                "SELECT signals FROM public.dash_autonomy_state "
                "WHERE project_slug = :s"
            ), {"s": slug}).fetchone()
        if row and row[0] is not None:
            val = row[0]
            return val if isinstance(val, dict) else json.loads(val)
    except Exception as e:
        log.debug("heartbeat: load_state failed for %s: %s", slug, e)
    return {}


def save_state(slug: str, signals: dict) -> None:
    """Upsert the signal snapshot for the project. Fail-soft."""
    from sqlalchemy import text
    try:
        eng = _write_engine()
        with eng.begin() as conn:
            conn.execute(text(
                "INSERT INTO public.dash_autonomy_state (project_slug, signals, updated_at) "
                "VALUES (:s, CAST(:j AS jsonb), now()) "
                "ON CONFLICT (project_slug) DO UPDATE "
                "SET signals = EXCLUDED.signals, updated_at = now()"
            ), {"s": slug, "j": json.dumps(signals or {})})
    except Exception as e:
        log.debug("heartbeat: save_state failed for %s: %s", slug, e)


def diff(old: dict, new: dict) -> list[str]:
    """Return the list of top-level signal keys that TRIPPED between snapshots.

    A key trips if it was added, removed, or changed value. dict/list values are
    compared by value (not identity). Returns a sorted list of key names.
    """
    old = old or {}
    new = new or {}
    tripped: set[str] = set()
    for key in set(old) | set(new):
        if key not in old or key not in new:
            tripped.add(key)
            continue
        if old[key] != new[key]:
            tripped.add(key)
    return sorted(tripped)


def journal(slug: str, tier: str, signal: str, action: str,
            detail: dict | None = None, tokens: int = 0) -> None:
    """Append one row to dash_autonomy_journal. Fail-soft."""
    from sqlalchemy import text
    try:
        eng = _write_engine()
        with eng.begin() as conn:
            conn.execute(text(
                "INSERT INTO public.dash_autonomy_journal "
                "(project_slug, tier, signal, action, detail, tokens) "
                "VALUES (:s, :tier, :signal, :action, CAST(:detail AS jsonb), :tokens)"
            ), {
                "s": slug,
                "tier": tier,
                "signal": signal,
                "action": action,
                "detail": json.dumps(detail) if detail is not None else None,
                "tokens": int(tokens or 0),
            })
    except Exception as e:
        log.debug("heartbeat: journal failed for %s (%s/%s): %s", slug, tier, signal, e)
