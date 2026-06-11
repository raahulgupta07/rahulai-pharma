"""
Autonomy API
============

Read-only endpoints over the token-frugal autonomy heartbeat core
(`dash/cron/heartbeat.py` + `dash/autonomy/*`). Exposes the per-project
signal snapshot and the T3 intent / budget journal so the UI can show what
the heartbeat is WATCHING and what it has acted on.

All read-only + fail-soft: a missing table returns an empty payload, never 500.
Auth mirrors app/learning.py (per-request user + project access check). Router
shares learning.py's "/api/projects" prefix so the final paths are
/api/projects/{slug}/autonomy/...
"""

import logging

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import create_engine as _sa_create_engine, text
from sqlalchemy.pool import NullPool

from db import db_url

router = APIRouter(prefix="/api/projects", tags=["Autonomy"])
_engine = _sa_create_engine(db_url, poolclass=NullPool)
logger = logging.getLogger(__name__)


def _iso_utc(v):
    """Serialize a DB timestamp as ISO-8601 UTC ending in 'Z'. (Mirrors
    app.learning._iso_utc — kept local to avoid import coupling.) Never throws."""
    if v is None:
        return None
    try:
        iso = getattr(v, "isoformat", None)
        s = v.isoformat() if callable(iso) else str(v)
        s = s.strip()
        if not s:
            return None
        s = s.replace(" ", "T", 1)
        if s.endswith("Z"):
            return s
        t_idx = s.find("T")
        time_part = s[t_idx + 1:] if t_idx >= 0 else s
        if "+" in time_part or "-" in time_part:
            return s
        return s + "Z"
    except Exception:
        try:
            return str(v) if v is not None else None
        except Exception:
            return None


def _get_user(request: Request) -> dict:
    user = getattr(getattr(request, 'state', None), 'user', None)
    if not user:
        raise HTTPException(401, "Not authenticated")
    return user


def _check_access(user: dict, slug: str):
    """Verify user has access to project (owner, shared, or super admin)."""
    from app.auth import check_project_permission
    perm = check_project_permission(user, slug)
    if not perm:
        raise HTTPException(403, "Access denied")


@router.get("/{slug}/autonomy/journal")
def autonomy_journal(slug: str, request: Request, limit: int = 50):
    """Recent autonomy journal rows for the project, newest-first. Fail-soft:
    missing table → {"journal": []}."""
    user = _get_user(request)
    _check_access(user, slug)
    try:
        limit = max(1, min(int(limit), 500))
    except Exception:
        limit = 50
    rows_out = []
    try:
        with _engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT id, ts, tier, signal, action, detail, tokens
                FROM public.dash_autonomy_journal
                WHERE project_slug = :s
                ORDER BY ts DESC, id DESC
                LIMIT :lim
            """), {"s": slug, "lim": limit}).fetchall()
        for r in rows:
            rows_out.append({
                "id": r[0],
                "ts": _iso_utc(r[1]),
                "tier": r[2],
                "signal": r[3],
                "action": r[4],
                "detail": r[5],
                "tokens": int(r[6] or 0),
            })
    except Exception as e:
        logger.debug("autonomy journal query failed for %s: %s", slug, e)
        return {"journal": []}
    return {"journal": rows_out}


@router.get("/{slug}/autonomy/state")
def autonomy_state(slug: str, request: Request):
    """Current signal snapshot for the project. Fail-soft: missing table /
    no row → {"signals": {}}."""
    user = _get_user(request)
    _check_access(user, slug)
    signals = {}
    updated_at = None
    try:
        with _engine.connect() as conn:
            row = conn.execute(text("""
                SELECT signals, updated_at
                FROM public.dash_autonomy_state
                WHERE project_slug = :s
            """), {"s": slug}).fetchone()
        if row:
            val = row[0]
            if isinstance(val, dict):
                signals = val
            elif val is not None:
                import json
                try:
                    signals = json.loads(val)
                except Exception:
                    signals = {}
            updated_at = _iso_utc(row[1])
    except Exception as e:
        logger.debug("autonomy state query failed for %s: %s", slug, e)
        return {"signals": {}}
    return {"signals": signals or {}, "updated_at": updated_at}
