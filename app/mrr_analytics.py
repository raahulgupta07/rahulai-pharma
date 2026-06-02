"""MRR / ARR / Retention REST endpoints — Tier 4 subscription analytics.

All endpoints viewer-or-higher except snapshot-now (admin/owner).
Per-project RLS via project_slug filter on dash_subscription_snapshots.

Endpoints:
    GET  /api/projects/{slug}/mrr/current
    GET  /api/projects/{slug}/mrr/breakdown
    GET  /api/projects/{slug}/mrr/trend
    GET  /api/projects/{slug}/mrr/retention
    GET  /api/projects/{slug}/mrr/cohort-survival
    GET  /api/projects/{slug}/mrr/schema-detection
    POST /api/projects/{slug}/mrr/snapshot-now
    GET  /api/projects/{slug}/mrr/snapshots
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["MRR"])


# ─────────────────── auth helpers ───────────────────


def _user(request: Request) -> dict:
    from app.auth import get_current_user
    u = get_current_user(request)
    if not u:
        raise HTTPException(401, "auth required")
    return u


def _check(user: dict, slug: str, role: str = "viewer") -> None:
    from app.auth import check_project_permission, SUPER_ADMIN
    res = check_project_permission(user, slug, role)
    if not res:
        if user.get("username") != SUPER_ADMIN:
            raise HTTPException(403, "permission denied")


# ─────────────────── helpers ───────────────────


def _parse_date(s: str | None, default: date | None = None) -> date:
    if s is None or s == "":
        if default is None:
            raise HTTPException(400, "date required")
        return default
    try:
        return datetime.fromisoformat(s.replace("Z", "")).date()
    except Exception:
        raise HTTPException(400, f"invalid date: {s!r}")


def _last_completed_month_bounds() -> tuple[date, date]:
    """Returns (first_of_last_month, last_day_of_last_month)."""
    today = date.today()
    first_this = date(today.year, today.month, 1)
    last_of_prev = first_this - timedelta(days=1)
    first_of_prev = date(last_of_prev.year, last_of_prev.month, 1)
    return first_of_prev, last_of_prev


def _bootstrap_snapshot_table() -> None:
    """Create dash_subscription_snapshots if it doesn't exist (idempotent)."""
    from db import get_sql_engine
    from sqlalchemy import text
    eng = get_sql_engine()
    ddl = """
    CREATE TABLE IF NOT EXISTS dash_subscription_snapshots (
      id BIGSERIAL PRIMARY KEY,
      project_slug TEXT NOT NULL,
      captured_at TIMESTAMPTZ DEFAULT now(),
      period_start DATE NOT NULL,
      period_end DATE NOT NULL,
      mrr NUMERIC,
      arr NUMERIC,
      new_mrr NUMERIC,
      expansion_mrr NUMERIC,
      contraction_mrr NUMERIC,
      churn_mrr NUMERIC,
      reactivation_mrr NUMERIC,
      net_new_mrr NUMERIC,
      gross_retention_pct NUMERIC,
      net_retention_pct NUMERIC,
      active_subscribers INT,
      churned_subscribers INT,
      metadata JSONB,
      UNIQUE(project_slug, period_start)
    );
    CREATE INDEX IF NOT EXISTS idx_sub_snapshots_slug_time
      ON dash_subscription_snapshots(project_slug, period_start DESC);
    """
    try:
        with eng.begin() as cn:
            cn.execute(text(ddl))
    except Exception as e:
        logger.warning(f"snapshot table bootstrap failed: {e}")


# ─────────────────── /current ───────────────────


@router.get("/projects/{slug}/mrr/current")
def mrr_current(slug: str, request: Request, as_of: str | None = None):
    user = _user(request)
    _check(user, slug, "viewer")
    from dash.tools.mrr_analytics import compute_mrr
    res = compute_mrr(slug, as_of_date=as_of)
    return res


# ─────────────────── /breakdown ───────────────────


@router.get("/projects/{slug}/mrr/breakdown")
def mrr_breakdown(
    slug: str,
    request: Request,
    period_start: str | None = None,
    period_end: str | None = None,
):
    user = _user(request)
    _check(user, slug, "viewer")
    from dash.tools.mrr_analytics import compute_mrr_breakdown
    if not period_start or not period_end:
        ps, pe = _last_completed_month_bounds()
    else:
        ps = _parse_date(period_start)
        pe = _parse_date(period_end)
    return compute_mrr_breakdown(slug, ps, pe)


# ─────────────────── /trend ───────────────────


@router.get("/projects/{slug}/mrr/trend")
def mrr_trend_endpoint(slug: str, request: Request, months: int = 12):
    user = _user(request)
    _check(user, slug, "viewer")
    from dash.tools.mrr_analytics import mrr_trend
    months = max(1, min(int(months), 36))
    return {"ok": True, "months": months, "series": mrr_trend(slug, months=months)}


# ─────────────────── /retention ───────────────────


@router.get("/projects/{slug}/mrr/retention")
def mrr_retention(
    slug: str,
    request: Request,
    period_start: str | None = None,
    period_end: str | None = None,
):
    user = _user(request)
    _check(user, slug, "viewer")
    from dash.tools.mrr_analytics import compute_retention
    if not period_start or not period_end:
        ps, pe = _last_completed_month_bounds()
    else:
        ps = _parse_date(period_start)
        pe = _parse_date(period_end)
    return compute_retention(slug, ps, pe)


# ─────────────────── /cohort-survival ───────────────────


@router.get("/projects/{slug}/mrr/cohort-survival")
def mrr_cohort_survival(
    slug: str,
    request: Request,
    cohort_window: str = "month",
    max_periods: int = 24,
):
    user = _user(request)
    _check(user, slug, "viewer")
    from dash.tools.mrr_analytics import cohort_survival
    max_periods = max(1, min(int(max_periods), 36))
    return cohort_survival(slug, cohort_window=cohort_window,
                           max_periods=max_periods)


# ─────────────────── /schema-detection ───────────────────


@router.get("/projects/{slug}/mrr/schema-detection")
def mrr_schema_detection(slug: str, request: Request):
    user = _user(request)
    _check(user, slug, "viewer")
    from dash.tools.mrr_analytics import detect_subscription_schema
    return detect_subscription_schema(slug)


# ─────────────────── POST /snapshot-now ───────────────────


@router.post("/projects/{slug}/mrr/snapshot-now")
def mrr_snapshot_now(slug: str, request: Request):
    user = _user(request)
    _check(user, slug, "admin")  # admin or super-admin only
    _bootstrap_snapshot_table()
    from dash.tools.mrr_analytics import (
        compute_mrr_breakdown, compute_retention,
    )
    ps, pe = _last_completed_month_bounds()
    breakdown = compute_mrr_breakdown(slug, ps, pe)
    if not breakdown.get("ok"):
        raise HTTPException(400,
            f"breakdown failed: {breakdown.get('error', 'unknown')}")
    retention = compute_retention(slug, ps, pe)

    from db import get_sql_engine
    from sqlalchemy import text

    sql = """
        INSERT INTO dash_subscription_snapshots (
            project_slug, period_start, period_end,
            mrr, arr, new_mrr, expansion_mrr, contraction_mrr,
            churn_mrr, reactivation_mrr, net_new_mrr,
            gross_retention_pct, net_retention_pct,
            active_subscribers, churned_subscribers, metadata
        ) VALUES (
            :slug, :ps, :pe,
            :mrr, :arr, :new_mrr, :exp, :con,
            :churn, :react, :net_new,
            :gross, :net,
            :active, :churned_subs, CAST(:meta AS jsonb)
        )
        ON CONFLICT (project_slug, period_start) DO UPDATE SET
            captured_at = now(),
            period_end = EXCLUDED.period_end,
            mrr = EXCLUDED.mrr,
            arr = EXCLUDED.arr,
            new_mrr = EXCLUDED.new_mrr,
            expansion_mrr = EXCLUDED.expansion_mrr,
            contraction_mrr = EXCLUDED.contraction_mrr,
            churn_mrr = EXCLUDED.churn_mrr,
            reactivation_mrr = EXCLUDED.reactivation_mrr,
            net_new_mrr = EXCLUDED.net_new_mrr,
            gross_retention_pct = EXCLUDED.gross_retention_pct,
            net_retention_pct = EXCLUDED.net_retention_pct,
            active_subscribers = EXCLUDED.active_subscribers,
            churned_subscribers = EXCLUDED.churned_subscribers,
            metadata = EXCLUDED.metadata
        RETURNING id
    """
    end_mrr = breakdown["end_mrr"]
    params = {
        "slug": slug,
        "ps": ps, "pe": pe,
        "mrr": end_mrr,
        "arr": round(end_mrr * 12.0, 2),
        "new_mrr": breakdown["new_mrr"],
        "exp": breakdown["expansion_mrr"],
        "con": breakdown["contraction_mrr"],
        "churn": breakdown["churn_mrr"],
        "react": breakdown["reactivation_mrr"],
        "net_new": breakdown["net_new_mrr"],
        "gross": retention.get("gross_retention_pct"),
        "net": retention.get("net_retention_pct"),
        "active": breakdown["active_subscribers"],
        "churned_subs": breakdown["churned_subscribers"],
        "meta": json.dumps({"source": "snapshot-now",
                            "schema": breakdown.get("schema", {})}),
    }
    eng = get_sql_engine()
    with eng.begin() as cn:
        row = cn.execute(text(sql), params).fetchone()
    return {"ok": True, "id": int(row[0]) if row else None,
            "period_start": ps.isoformat(),
            "period_end": pe.isoformat(),
            "breakdown": breakdown,
            "retention": retention}


# ─────────────────── GET /snapshots ───────────────────


@router.get("/projects/{slug}/mrr/snapshots")
def mrr_snapshots(slug: str, request: Request, limit: int = 24):
    user = _user(request)
    _check(user, slug, "viewer")
    _bootstrap_snapshot_table()
    from db import get_sql_engine
    from sqlalchemy import text
    limit = max(1, min(int(limit), 200))
    eng = get_sql_engine()
    with eng.begin() as cn:
        rows = cn.execute(text(
            "SELECT id, period_start, period_end, mrr, arr, "
            "new_mrr, expansion_mrr, contraction_mrr, churn_mrr, "
            "reactivation_mrr, net_new_mrr, gross_retention_pct, "
            "net_retention_pct, active_subscribers, churned_subscribers, "
            "captured_at "
            "FROM dash_subscription_snapshots WHERE project_slug = :s "
            "ORDER BY period_start DESC LIMIT :l"
        ), {"s": slug, "l": limit}).mappings().all()
    return {"ok": True, "snapshots": [dict(r) for r in rows]}


__all__ = ["router"]
