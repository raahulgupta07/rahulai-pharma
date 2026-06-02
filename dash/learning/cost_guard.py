"""Cost guard — enforce per-project daily cost ceiling.

Tracks today's spend by summing dash_self_learning_runs.cost_usd for
project where started_at >= today (UTC midnight).

When today_spend >= daily_cost_cap_usd, returns over_budget=True.
Cycle should skip remaining work and set cost_paused_until = next midnight UTC.
"""
from __future__ import annotations
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class CostStatus:
    project_slug: Optional[str]
    daily_cap_usd: float
    today_spend_usd: float
    over_budget: bool
    paused_until: Optional[str] = None


def get_status(project_slug: Optional[str], dash_engine=None) -> CostStatus:
    """Read project cap + today's accumulated spend."""
    status = CostStatus(
        project_slug=project_slug, daily_cap_usd=0.0,
        today_spend_usd=0.0, over_budget=False,
    )
    try:
        from sqlalchemy import text
        from db.session import get_sql_engine
        eng = dash_engine or get_sql_engine()
        with eng.connect() as conn:
            # Cap from project
            if project_slug:
                r = conn.execute(text(
                    "SELECT COALESCE(daily_cost_cap_usd, 1.0), "
                    "       cost_paused_until "
                    "FROM public.dash_projects "
                    "WHERE slug = :s"
                ), {"s": project_slug}).fetchone()
                if r:
                    status.daily_cap_usd = float(r[0] or 1.0)
                    if r[1]:
                        status.paused_until = r[1].isoformat() if hasattr(r[1], 'isoformat') else str(r[1])
            else:
                status.daily_cap_usd = 5.0  # central default

            # Today's spend
            r2 = conn.execute(text(
                "SELECT COALESCE(SUM(cost_usd), 0) "
                "FROM public.dash_self_learning_runs "
                "WHERE (project_slug = :s OR (:s IS NULL AND project_slug IS NULL)) "
                "  AND started_at >= DATE_TRUNC('day', NOW() AT TIME ZONE 'UTC')"
            ), {"s": project_slug}).fetchone()
            status.today_spend_usd = float((r2 or [0])[0] or 0)

        # Fall back to admin-settings default cap when project has no cap.
        # Only treat exactly-zero as "unset"; negative caps explicitly mean unlimited.
        if status.daily_cap_usd == 0 and project_slug:
            try:
                from dash.admin.settings import get_setting
                default_cap = float(
                    get_setting(
                        "daily_cost_cap_default_usd",
                        project_slug=project_slug,
                    ) or 1.0
                )
                status.daily_cap_usd = default_cap
            except Exception:
                pass

        # 0 cap = unlimited
        if status.daily_cap_usd <= 0:
            status.over_budget = False
        else:
            status.over_budget = status.today_spend_usd >= status.daily_cap_usd
    except Exception as e:
        logger.warning(f"cost get_status failed: {e}")

    # Best-effort: notify project owner when daily cost passes 80% of cap
    try:
        if project_slug and status.daily_cap_usd > 0:
            pct = (status.today_spend_usd / status.daily_cap_usd) * 100.0
            if pct >= 80.0:
                from sqlalchemy import text as _nt
                from db.session import get_sql_engine as _gse
                from app.auth import notify_user  # type: ignore
                eng2 = _gse()
                with eng2.connect() as conn:
                    # Throttle: only one warn per project per day
                    already = conn.execute(_nt(
                        "SELECT 1 FROM public.dash_notifications n "
                        "JOIN public.dash_projects p ON p.user_id = n.user_id "
                        "WHERE p.slug = :s AND n.title = 'Daily AI cost cap near limit' "
                        "  AND n.created_at >= DATE_TRUNC('day', NOW() AT TIME ZONE 'UTC')"
                    ), {"s": project_slug}).fetchone()
                    if not already:
                        r = conn.execute(_nt(
                            "SELECT user_id FROM public.dash_projects WHERE slug = :s"
                        ), {"s": project_slug}).fetchone()
                        if r and r[0]:
                            notify_user(
                                int(r[0]),
                                "Daily AI cost cap near limit",
                                f"${status.today_spend_usd:.2f} of ${status.daily_cap_usd:.2f} ({pct:.0f}%)",
                                "warn",
                            )
    except Exception:
        pass

    return status


def get_status_with_federation_weight(project_slug, dash_engine=None,
                                         federation_weight: float = 2.0) -> CostStatus:
    """Like get_status but applies 2x weight to federated queries."""
    base = get_status(project_slug, dash_engine=dash_engine)
    # Add today's federation cost (weighted)
    try:
        from sqlalchemy import text
        from db.session import get_sql_engine
        eng = dash_engine or get_sql_engine()
        with eng.connect() as conn:
            r = conn.execute(text(
                "SELECT COALESCE(SUM(cost_usd), 0) "
                "FROM public.dash_audit_log "
                "WHERE project_slug = :s AND action = 'federated_query' "
                " AND created_at >= DATE_TRUNC('day', NOW() AT TIME ZONE 'UTC')"
            ), {"s": project_slug}).fetchone()
        fed_cost = float((r or [0])[0] or 0)
        base.today_spend_usd += fed_cost * (federation_weight - 1.0)
        if base.daily_cap_usd > 0:
            base.over_budget = base.today_spend_usd >= base.daily_cap_usd
    except Exception:
        pass
    return base


def pause_until_tomorrow(project_slug: str, dash_engine=None) -> bool:
    """Set cost_paused_until = next UTC midnight."""
    try:
        from sqlalchemy import text
        from db.session import get_sql_engine
        eng = dash_engine or get_sql_engine()
        next_midnight = (datetime.utcnow() + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        with eng.connect() as conn:
            conn.execute(text(
                "UPDATE public.dash_projects "
                "SET cost_paused_until = :ts "
                "WHERE slug = :s"
            ), {"ts": next_midnight, "s": project_slug})
            conn.commit()
        return True
    except Exception as e:
        logger.warning(f"pause_until_tomorrow failed: {e}")
        return False


def history_7d(project_slug: Optional[str], dash_engine=None) -> list[dict]:
    """7-day cost timeline."""
    try:
        from sqlalchemy import text
        from db.session import get_sql_engine
        eng = dash_engine or get_sql_engine()
        with eng.connect() as conn:
            rows = conn.execute(text(
                "SELECT DATE(started_at) AS day, COALESCE(SUM(cost_usd), 0) AS spend "
                "FROM public.dash_self_learning_runs "
                "WHERE (project_slug = :s OR (:s IS NULL AND project_slug IS NULL)) "
                "  AND started_at > NOW() - INTERVAL '7 days' "
                "GROUP BY DATE(started_at) ORDER BY day ASC"
            ), {"s": project_slug}).fetchall()
        return [{"day": r[0].isoformat() if hasattr(r[0], 'isoformat') else str(r[0]),
                 "spend": float(r[1] or 0)} for r in rows]
    except Exception as e:
        logger.warning(f"history_7d failed: {e}")
        return []
