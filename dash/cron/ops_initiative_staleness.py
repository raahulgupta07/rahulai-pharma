"""
Ops Initiative Staleness daemon — flags in_progress initiatives w/ no update
in 90 days. Writes brain rows category='ops_alert' key='stale_initiative:{id}'.

Tunables:
  OPS_STALENESS_DAEMON_DISABLED=1 → skip
  OPS_STALENESS_INTERVAL_SECONDS  → default 604800 (7d), floor 3600
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

try:
    from dash.obs.trace import trace_span
except Exception:  # noqa: BLE001
    from contextlib import contextmanager as _cm

    @_cm
    def trace_span(*_a, **_k):  # type: ignore
        yield None


DEFAULT_INTERVAL_SECONDS = 7 * 24 * 60 * 60
MIN_INTERVAL_SECONDS = 3600


def _sql_engine():
    try:
        from db.session import get_sql_engine
        return get_sql_engine()
    except Exception:
        logger.exception("ops_initiative_staleness: get_sql_engine failed")
        return None


def _write_engine():
    try:
        from db.session import get_write_engine
        return get_write_engine()
    except Exception:
        logger.exception("ops_initiative_staleness: get_write_engine failed")
        return None


def _is_disabled() -> bool:
    return (os.getenv("OPS_STALENESS_DAEMON_DISABLED", "").lower()
            in ("1", "true", "yes"))


def _interval_seconds() -> int:
    raw = os.getenv("OPS_STALENESS_INTERVAL_SECONDS", "")
    try:
        v = int(raw)
    except Exception:
        v = DEFAULT_INTERVAL_SECONDS
    if v < MIN_INTERVAL_SECONDS:
        v = MIN_INTERVAL_SECONDS
    return v


async def run_once() -> dict[str, Any]:
    """SELECT stale initiatives, write brain row per finding.
    Brain unique constraint on (project_slug, name, category) makes this
    naturally idempotent (UPDATE on conflict).
    """
    out: dict[str, Any] = {"ok": True, "stale": 0, "alerted": 0,
                           "errors": []}
    sql_eng = _sql_engine()
    wr_eng = _write_engine()
    if sql_eng is None or wr_eng is None:
        out["ok"] = False
        out["errors"].append("no_engine")
        return out

    def _list_stale() -> list[dict[str, Any]]:
        from sqlalchemy import text as _t
        try:
            with sql_eng.connect() as cn:
                rows = cn.execute(_t("""
                    SELECT i.id, i.portco_id, i.title, i.updated_at,
                           p.project_slug, p.legal_name,
                           EXTRACT(DAY FROM now() - i.updated_at)::int AS days_stale
                    FROM dash.dash_portco_initiatives i
                    JOIN dash.dash_portco p ON p.id = i.portco_id
                    WHERE i.status = 'in_progress'
                      AND i.updated_at < now() - interval '90 days'
                    LIMIT 500
                """)).fetchall()
            return [
                {
                    "id": str(r[0]), "portco_id": str(r[1]), "title": r[2],
                    "updated_at": r[3].isoformat() if r[3] else None,
                    "project_slug": r[4], "legal_name": r[5],
                    "days_stale": int(r[6]) if r[6] is not None else 0,
                } for r in rows
            ]
        except Exception:
            logger.exception("ops_initiative_staleness: list failed")
            return []

    stale = await asyncio.to_thread(_list_stale)
    out["stale"] = len(stale)

    def _write_brain_row(item: dict[str, Any]) -> bool:
        from sqlalchemy import text as _t
        key = f"stale_initiative:{item['id']}"
        value = {
            "portco_id": item["portco_id"],
            "legal_name": item.get("legal_name"),
            "title": item.get("title"),
            "days_stale": item.get("days_stale"),
            "updated_at": item.get("updated_at"),
        }
        try:
            with wr_eng.begin() as cn:
                # Try ON CONFLICT first (preferred path).
                try:
                    cn.execute(_t("""
                        INSERT INTO public.dash_company_brain
                            (project_slug, name, category, definition, metadata)
                        VALUES (:p, :n, 'ops_alert',
                                'Stale in-progress initiative',
                                CAST(:m AS jsonb))
                        ON CONFLICT (project_slug, name, category)
                        DO UPDATE SET metadata = CAST(:m AS jsonb),
                                      updated_at = now()
                    """), {"p": item["project_slug"], "n": key,
                           "m": json.dumps(value)})
                    return True
                except Exception:
                    # No unique idx — manual upsert.
                    existing = cn.execute(_t("""
                        SELECT id FROM public.dash_company_brain
                        WHERE project_slug = :p AND name = :n
                          AND category = 'ops_alert'
                        LIMIT 1
                    """), {"p": item["project_slug"], "n": key}).fetchone()
                    if existing:
                        cn.execute(_t("""
                            UPDATE public.dash_company_brain
                            SET metadata = CAST(:m AS jsonb),
                                definition = 'Stale in-progress initiative'
                            WHERE id = :id
                        """), {"m": json.dumps(value), "id": existing[0]})
                    else:
                        cn.execute(_t("""
                            INSERT INTO public.dash_company_brain
                                (project_slug, name, category, definition, metadata)
                            VALUES (:p, :n, 'ops_alert',
                                    'Stale in-progress initiative',
                                    CAST(:m AS jsonb))
                        """), {"p": item["project_slug"], "n": key,
                               "m": json.dumps(value)})
            return True
        except Exception:
            logger.exception("ops_initiative_staleness: brain insert failed")
            return False

    for item in stale:
        try:
            ok = await asyncio.to_thread(_write_brain_row, item)
            if ok:
                out["alerted"] += 1
        except Exception as e:  # noqa: BLE001
            out["errors"].append({"id": item.get("id"), "error": str(e)})

    logger.info(
        "ops_initiative_staleness: cycle_done stale=%d alerted=%d errors=%d",
        out["stale"], out["alerted"], len(out["errors"]),
    )
    return out


async def ops_staleness_loop() -> None:
    if _is_disabled():
        logger.info("ops_initiative_staleness: disabled via env")
        return
    interval = _interval_seconds()
    logger.info("ops_initiative_staleness: starting (interval=%ds)", interval)
    while True:
        try:
            with trace_span("cron.ops_initiative_staleness", kind="cron"):
                await run_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("ops_initiative_staleness: cycle crashed")
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise


__all__ = ["run_once", "ops_staleness_loop"]
