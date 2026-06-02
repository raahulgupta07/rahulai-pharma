"""
Accuracy Trend API — read-only admin endpoint.

Queries dash.dash_eval_runs and public.dash_verified_scores to compute
pass-rate trends. Neither table has a 'tier' column, so by_tier returns
{} unless metadata.tier exists in a JSONB field.

Endpoint: GET /api/accuracy/trend?days=30
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import text

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/accuracy", tags=["accuracy"])


def _empty_response() -> dict[str, Any]:
    return {
        "series": [],
        "by_tier": {},
        "overall": 0.0,
        "last_run_at": None,
    }


@router.get("/trend")
def accuracy_trend(days: int = Query(30, ge=1, le=365)) -> dict[str, Any]:
    """
    Combined accuracy trend from eval runs + verified scores.

    Returns:
        series:      [{date, pass_rate, tier, n}, ...]  (one row per date+source)
        by_tier:     {tier: pass_rate}                   (empty if no tier column)
        overall:     float                               (weighted pass rate)
        last_run_at: ISO timestamp of most recent run
    """
    try:
        from db.session import get_sql_engine
    except Exception as e:
        logger.warning("get_sql_engine import failed: %s", e)
        return _empty_response()

    try:
        engine = get_sql_engine()
    except Exception as e:
        logger.warning("get_sql_engine() failed: %s", e)
        return _empty_response()

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    series: list[dict[str, Any]] = []
    overall_pass = 0
    overall_total = 0
    last_run_at: datetime | None = None

    # --- dash_eval_runs: aggregate per-day pass rate ---
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT DATE(started_at) AS d,
                           SUM(passed)::int AS passed,
                           SUM(total_cases)::int AS total,
                           MAX(started_at) AS last_at
                    FROM dash.dash_eval_runs
                    WHERE started_at >= :cutoff
                      AND total_cases > 0
                    GROUP BY DATE(started_at)
                    ORDER BY d
                    """
                ),
                {"cutoff": cutoff},
            ).fetchall()
            for r in rows:
                total = int(r.total or 0)
                passed = int(r.passed or 0)
                if total <= 0:
                    continue
                series.append(
                    {
                        "date": r.d.isoformat() if r.d else None,
                        "pass_rate": round(passed / total, 4),
                        "tier": "eval",
                        "n": total,
                    }
                )
                overall_pass += passed
                overall_total += total
                if r.last_at and (last_run_at is None or r.last_at > last_run_at):
                    last_run_at = r.last_at
    except Exception as e:
        logger.warning("dash_eval_runs query failed: %s", e)

    # --- dash_verified_scores: pass rate of 'pass' / ('pass' + 'fail') per day ---
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT DATE(created_at) AS d,
                           SUM(CASE WHEN verified = 'pass' THEN 1 ELSE 0 END)::int AS passed,
                           SUM(CASE WHEN verified IN ('pass','fail') THEN 1 ELSE 0 END)::int AS checked,
                           MAX(created_at) AS last_at
                    FROM public.dash_verified_scores
                    WHERE created_at >= :cutoff
                    GROUP BY DATE(created_at)
                    ORDER BY d
                    """
                ),
                {"cutoff": cutoff},
            ).fetchall()
            for r in rows:
                checked = int(r.checked or 0)
                passed = int(r.passed or 0)
                if checked <= 0:
                    continue
                series.append(
                    {
                        "date": r.d.isoformat() if r.d else None,
                        "pass_rate": round(passed / checked, 4),
                        "tier": "verified",
                        "n": checked,
                    }
                )
                overall_pass += passed
                overall_total += checked
                if r.last_at and (last_run_at is None or r.last_at > last_run_at):
                    last_run_at = r.last_at
    except Exception as e:
        logger.warning("dash_verified_scores query failed: %s", e)

    # Sort series by date
    series.sort(key=lambda x: (x.get("date") or "", x.get("tier") or ""))

    # by_tier: aggregate across the window
    by_tier: dict[str, float] = {}
    tier_totals: dict[str, tuple[int, int]] = {}
    for row in series:
        t = row["tier"]
        p, n = tier_totals.get(t, (0, 0))
        tier_totals[t] = (p + int(row["pass_rate"] * row["n"]), n + row["n"])
    for t, (p, n) in tier_totals.items():
        if n > 0:
            by_tier[t] = round(p / n, 4)

    overall = round(overall_pass / overall_total, 4) if overall_total > 0 else 0.0

    return {
        "series": series,
        "by_tier": by_tier,
        "overall": overall,
        "last_run_at": last_run_at.isoformat() if last_run_at else None,
    }
