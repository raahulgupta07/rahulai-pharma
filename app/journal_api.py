"""
Daily AI-summarized journal per project (Obsidian-style daily notes).

Storage: public.dash_journal — UNIQUE(project_slug, journal_date).
Generation: scripts/daily_journal.py CLI (cron) or POST /api/journal/{slug}/generate.

Style mirrors app/golden_api.py — fail-soft, parameterized SQL, PgBouncer-safe
CAST(:x AS jsonb), writes via get_write_engine().
"""
from __future__ import annotations

import json
import logging
from datetime import date as date_cls, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/journal", tags=["journal"])


def _read_engine():
    try:
        from db.session import get_sql_engine  # type: ignore
        return get_sql_engine()
    except Exception as e:  # pragma: no cover
        raise HTTPException(503, f"db unavailable: {e}")


def _write_engine():
    try:
        from db.session import get_write_engine  # type: ignore
        return get_write_engine()
    except Exception:
        from db.session import get_sql_engine  # type: ignore
        return get_sql_engine()


def _parse_date(s: str | None) -> date_cls:
    if not s:
        return date_cls.today()
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(400, "date must be YYYY-MM-DD")


def _row_to_dict(row) -> dict[str, Any]:
    stats = row.stats
    if isinstance(stats, str):
        try:
            stats = json.loads(stats)
        except Exception:
            stats = {}
    return {
        "id": str(row.id),
        "project_slug": row.project_slug,
        "journal_date": str(row.journal_date),
        "stats": stats or {},
        "summary_md": row.summary_md or "",
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


# ---------- endpoints ----------


@router.get("/{slug}")
def get_journal(slug: str, date: str | None = Query(None)) -> dict[str, Any]:
    """Fetch journal entry for slug + date (default today). 404 if missing."""
    d = _parse_date(date)
    try:
        eng = _read_engine()
        with eng.connect() as conn:
            row = conn.execute(text(
                "SELECT id, project_slug, journal_date, stats, summary_md, created_at "
                "FROM public.dash_journal "
                "WHERE project_slug = :s AND journal_date = :d"
            ), {"s": slug, "d": d}).fetchone()
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"get_journal failed: {e}")
        raise HTTPException(503, f"journal unavailable: {e}")

    if not row:
        raise HTTPException(404, f"no journal for {slug} on {d}")
    return _row_to_dict(row)


@router.get("/{slug}/list")
def list_journal(slug: str, limit: int = Query(30, ge=1, le=200)) -> dict[str, Any]:
    """Recent journals for slug, newest first."""
    try:
        eng = _read_engine()
        with eng.connect() as conn:
            rows = conn.execute(text(
                "SELECT id, project_slug, journal_date, stats, summary_md, created_at "
                "FROM public.dash_journal "
                "WHERE project_slug = :s "
                "ORDER BY journal_date DESC "
                "LIMIT :n"
            ), {"s": slug, "n": int(limit)}).fetchall()
    except Exception as e:
        logger.exception(f"list_journal failed: {e}")
        raise HTTPException(503, f"journal unavailable: {e}")

    entries = [_row_to_dict(r) for r in rows]
    return {"project_slug": slug, "count": len(entries), "entries": entries}


@router.post("/{slug}/generate")
def generate_journal(slug: str, date: str | None = Query(None)) -> dict[str, Any]:
    """Manually trigger generation for given date (default yesterday)."""
    if date:
        d = _parse_date(date)
    else:
        d = date_cls.today() - timedelta(days=1)

    try:
        # Lazy import to avoid loading heavy deps on cold endpoints.
        from scripts.daily_journal import generate_for_project  # type: ignore
    except Exception as e:
        logger.exception(f"daily_journal import failed: {e}")
        raise HTTPException(503, f"journal generator unavailable: {e}")

    try:
        result = generate_for_project(slug, d)
    except Exception as e:
        logger.exception(f"generate_journal failed for {slug} {d}: {e}")
        raise HTTPException(500, f"generate failed: {e}")

    return {"ok": True, "project_slug": slug, "journal_date": str(d), "result": result}
