"""Daily MRR snapshot daemon — Tier 4.

For every project with the SaaS template applied OR feature_config.tools.mrr_analytics
enabled, computes last-completed-month breakdown + retention and UPSERTs
into ``dash_subscription_snapshots``.

Daily at 04:30 UTC (env ``MRR_SNAPSHOT_INTERVAL_SECONDS`` overrides cadence,
``MRR_SNAPSHOT_DISABLED=1`` opts out — typically when a K8s CronJob is
calling /api/projects/{slug}/mrr/snapshot-now instead).

Idempotent via UNIQUE(project_slug, period_start).
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import date, datetime, time as dtime, timedelta, timezone

log = logging.getLogger(__name__)

# Observability tracing (fail-soft; no-op if unavailable or TRACING_DISABLED).
try:
    from dash.obs.trace import trace_span
except Exception:  # noqa: BLE001
    from contextlib import contextmanager as _cm

    @_cm
    def trace_span(*_a, **_k):  # type: ignore
        yield None

_DAY = 86400


def _last_completed_month_bounds() -> tuple[date, date]:
    today = date.today()
    first_this = date(today.year, today.month, 1)
    last_of_prev = first_this - timedelta(days=1)
    first_of_prev = date(last_of_prev.year, last_of_prev.month, 1)
    return first_of_prev, last_of_prev


def _bootstrap_table() -> None:
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
      mrr NUMERIC, arr NUMERIC, new_mrr NUMERIC, expansion_mrr NUMERIC,
      contraction_mrr NUMERIC, churn_mrr NUMERIC, reactivation_mrr NUMERIC,
      net_new_mrr NUMERIC, gross_retention_pct NUMERIC, net_retention_pct NUMERIC,
      active_subscribers INT, churned_subscribers INT, metadata JSONB,
      UNIQUE(project_slug, period_start)
    );
    CREATE INDEX IF NOT EXISTS idx_sub_snapshots_slug_time
      ON dash_subscription_snapshots(project_slug, period_start DESC);
    """
    try:
        with eng.begin() as cn:
            cn.execute(text(ddl))
    except Exception as e:
        log.warning(f"snapshot table bootstrap failed: {e}")


def _list_eligible_projects() -> list[str]:
    """Return slugs with SaaS template applied OR mrr_analytics feature toggle on."""
    from db import get_sql_engine
    from sqlalchemy import text
    eng = get_sql_engine()
    slugs: set[str] = set()

    # 1) Projects with SaaS template applied
    try:
        with eng.begin() as cn:
            rows = cn.execute(text(
                "SELECT DISTINCT project_slug FROM dash_template_expectations "
                "WHERE template_name = 'saas' OR "
                "(metadata->>'category' = 'subscription' OR "
                " metadata->>'category' = 'tech_saas')"
            )).fetchall()
            for r in rows:
                if r[0]:
                    slugs.add(r[0])
    except Exception as e:
        log.debug(f"saas-template scan skipped: {e}")

    # 2) Projects with feature_config.tools.mrr_analytics = true
    try:
        with eng.begin() as cn:
            rows = cn.execute(text(
                "SELECT slug FROM dash_projects "
                "WHERE feature_config IS NOT NULL "
                "AND feature_config->'tools'->>'mrr_analytics' IN ('true','1','yes')"
            )).fetchall()
            for r in rows:
                if r[0]:
                    slugs.add(r[0])
    except Exception as e:
        log.debug(f"feature_config scan skipped: {e}")

    return sorted(slugs)


def _snapshot_one(slug: str) -> dict:
    """Compute + UPSERT one project's breakdown for last completed month."""
    import json
    from db import get_sql_engine
    from sqlalchemy import text
    from dash.tools.mrr_analytics import (
        compute_mrr_breakdown, compute_retention, detect_subscription_schema,
    )

    schema_info = detect_subscription_schema(slug)
    if not schema_info.get("found"):
        return {"slug": slug, "ok": False, "skipped": True,
                "reason": "schema not detected"}

    ps, pe = _last_completed_month_bounds()
    b = compute_mrr_breakdown(slug, ps, pe)
    if not b.get("ok"):
        return {"slug": slug, "ok": False, "error": b.get("error")}
    r = compute_retention(slug, ps, pe)

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
    """
    end_mrr = b["end_mrr"]
    params = {
        "slug": slug, "ps": ps, "pe": pe,
        "mrr": end_mrr,
        "arr": round(end_mrr * 12.0, 2),
        "new_mrr": b["new_mrr"], "exp": b["expansion_mrr"],
        "con": b["contraction_mrr"], "churn": b["churn_mrr"],
        "react": b["reactivation_mrr"], "net_new": b["net_new_mrr"],
        "gross": r.get("gross_retention_pct") if r.get("ok") else None,
        "net": r.get("net_retention_pct") if r.get("ok") else None,
        "active": b["active_subscribers"],
        "churned_subs": b["churned_subscribers"],
        "meta": json.dumps({"source": "cron", "schema": schema_info}),
    }
    eng = get_sql_engine()
    with eng.begin() as cn:
        cn.execute(text(sql), params)
    return {"slug": slug, "ok": True,
            "period_start": ps.isoformat(),
            "mrr": end_mrr, "net_new": b["net_new_mrr"]}


async def run_once() -> dict:
    """One pass over all eligible projects. Returns aggregate report."""
    _bootstrap_table()
    slugs = _list_eligible_projects()
    results = []
    for s in slugs:
        try:
            results.append(_snapshot_one(s))
        except Exception as e:
            log.exception(f"snapshot failed for {s}")
            results.append({"slug": s, "ok": False, "error": str(e)})
    log.info("mrr_snapshot run: %d projects processed", len(slugs))
    return {"projects": len(slugs), "results": results}


def _seconds_until_next_run(target_hour_utc: int = 4,
                             target_minute_utc: int = 30) -> float:
    now = datetime.now(tz=timezone.utc)
    target_today = datetime.combine(
        now.date(), dtime(target_hour_utc, target_minute_utc, tzinfo=timezone.utc)
    )
    if target_today <= now:
        target_today = target_today + timedelta(days=1)
    return max(1.0, (target_today - now).total_seconds())


async def mrr_snapshot_loop() -> None:
    """Forever-loop: wait until 04:30 UTC, run once, sleep. Cancellation-safe."""
    if os.environ.get("MRR_SNAPSHOT_DISABLED") in ("1", "true", "TRUE", "yes"):
        log.info("MRR_SNAPSHOT_DISABLED set — daemon not started")
        return

    interval_env = os.environ.get("MRR_SNAPSHOT_INTERVAL_SECONDS")
    fixed_interval: float | None = None
    if interval_env:
        try:
            fixed_interval = max(60.0, float(interval_env))
        except ValueError:
            fixed_interval = None

    while True:
        try:
            wait_s = fixed_interval if fixed_interval else _seconds_until_next_run(4, 30)
            log.info("mrr_snapshot_loop: sleeping %.0fs", wait_s)
            await asyncio.sleep(wait_s)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("mrr_snapshot_loop sleep error")
            await asyncio.sleep(_DAY)
            continue

        try:
            with trace_span("cron.mrr_snapshot", kind="cron"):
                await run_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("mrr_snapshot_loop run_once failed")


__all__ = ["run_once", "mrr_snapshot_loop"]
