"""
Supply Score daemon — every 24h, score every supplier and write one row.

Mirrors dash/cron/ops_anomaly_scan.py: env-gate, tunable interval,
worker-rank gated by app/main.py master gate.

Tunables:
  SUPPLY_SCORE_DAEMON_DISABLED=1 → skip
  SUPPLY_SCORE_INTERVAL_SECONDS  → default 86400 (24h), floor 3600
"""
from __future__ import annotations

import asyncio
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


DEFAULT_INTERVAL_SECONDS = 24 * 60 * 60
MIN_INTERVAL_SECONDS = 3600


def _sql_engine():
    try:
        from db.session import get_sql_engine
        return get_sql_engine()
    except Exception:
        logger.exception("supply_score: get_sql_engine failed")
        return None


def _is_disabled() -> bool:
    return (os.getenv("SUPPLY_SCORE_DAEMON_DISABLED", "").lower()
            in ("1", "true", "yes"))


def _interval_seconds() -> int:
    raw = os.getenv("SUPPLY_SCORE_INTERVAL_SECONDS", "")
    try:
        v = int(raw)
    except Exception:
        v = DEFAULT_INTERVAL_SECONDS
    if v < MIN_INTERVAL_SECONDS:
        v = MIN_INTERVAL_SECONDS
    return v


async def run_once() -> dict[str, Any]:
    out: dict[str, Any] = {
        "ok": True,
        "suppliers_scored": 0,
        "errors": [],
    }
    eng = _sql_engine()
    if eng is None:
        out["ok"] = False
        out["errors"].append("no_engine")
        return out

    def _list_suppliers() -> list[str]:
        from sqlalchemy import text as _t
        try:
            with eng.connect() as cn:
                rows = cn.execute(_t(
                    "SELECT id FROM dash.dash_suppliers"
                )).fetchall()
            return [str(r[0]) for r in rows]
        except Exception:
            logger.exception("supply_score: list suppliers failed")
            return []

    ids = await asyncio.to_thread(_list_suppliers)

    try:
        from dash.tools.supply_tools import score_supplier
    except Exception as e:
        logger.exception("supply_score: import score_supplier failed")
        out["ok"] = False
        out["errors"].append(f"import_failed: {e}")
        return out

    for sid in ids:
        try:
            r = await asyncio.to_thread(score_supplier, sid)
        except Exception as e:  # noqa: BLE001
            logger.exception("supply_score: supplier crashed sid=%s", sid)
            out["errors"].append({"supplier_id": sid, "error": str(e)})
            continue
        if r.get("ok"):
            out["suppliers_scored"] += 1

    logger.info(
        "supply_score: cycle_done scored=%d errors=%d",
        out["suppliers_scored"], len(out["errors"]),
    )
    return out


async def supply_score_loop() -> None:
    if _is_disabled():
        logger.info("supply_score: disabled via env")
        return
    interval = _interval_seconds()
    logger.info("supply_score: starting (interval=%ds)", interval)
    while True:
        try:
            with trace_span("cron.supply_score", kind="cron"):
                await run_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("supply_score: cycle crashed")
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise


__all__ = ["run_once", "supply_score_loop"]
