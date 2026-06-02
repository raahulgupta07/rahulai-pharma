"""
Ops Anomaly Scan daemon — periodic z-score scan across active portcos.

Mirrors dash/cron/venture_rescore.py: env-gate, tunable interval, run_once
public, fail-soft per portco.

Tunables:
  OPS_ANOMALY_DAEMON_DISABLED=1 → skip
  OPS_ANOMALY_INTERVAL_SECONDS  → default 604800 (7d), floor 3600
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


DEFAULT_INTERVAL_SECONDS = 7 * 24 * 60 * 60
MIN_INTERVAL_SECONDS = 3600


def _sql_engine():
    try:
        from db.session import get_sql_engine
        return get_sql_engine()
    except Exception:
        logger.exception("ops_anomaly_scan: get_sql_engine failed")
        return None


def _is_disabled() -> bool:
    return (os.getenv("OPS_ANOMALY_DAEMON_DISABLED", "").lower()
            in ("1", "true", "yes"))


def _interval_seconds() -> int:
    raw = os.getenv("OPS_ANOMALY_INTERVAL_SECONDS", "")
    try:
        v = int(raw)
    except Exception:
        v = DEFAULT_INTERVAL_SECONDS
    if v < MIN_INTERVAL_SECONDS:
        v = MIN_INTERVAL_SECONDS
    return v


async def run_once() -> dict[str, Any]:
    """Scan all status='active' portcos, write new anomaly rows.
    Idempotent on (portco_id, metric_name, period) via ops_tools.detect_anomalies.
    """
    out: dict[str, Any] = {
        "ok": True,
        "portcos_scanned": 0,
        "anomalies_detected": 0,
        "errors": [],
    }
    eng = _sql_engine()
    if eng is None:
        out["ok"] = False
        out["errors"].append("no_engine")
        return out

    def _list_active() -> list[str]:
        from sqlalchemy import text as _t
        try:
            with eng.connect() as cn:
                rows = cn.execute(_t(
                    "SELECT id FROM dash.dash_portco WHERE status = 'active'"
                )).fetchall()
            return [str(r[0]) for r in rows]
        except Exception:
            logger.exception("ops_anomaly_scan: list active failed")
            return []

    portco_ids = await asyncio.to_thread(_list_active)

    try:
        from dash.tools.ops_tools import detect_anomalies
    except Exception as e:
        logger.exception("ops_anomaly_scan: import detect_anomalies failed")
        out["ok"] = False
        out["errors"].append(f"import_failed: {e}")
        return out

    for pid in portco_ids:
        try:
            r = await asyncio.to_thread(detect_anomalies, pid, 2.0)
        except Exception as e:  # noqa: BLE001
            logger.exception("ops_anomaly_scan: portco crashed pid=%s", pid)
            out["errors"].append({"portco_id": pid, "error": str(e)})
            continue
        out["portcos_scanned"] += 1
        if r.get("ok"):
            out["anomalies_detected"] += int(r.get("detected") or 0)

    logger.info(
        "ops_anomaly_scan: cycle_done portcos=%d detected=%d errors=%d",
        out["portcos_scanned"], out["anomalies_detected"], len(out["errors"]),
    )
    return out


async def ops_anomaly_loop() -> None:
    if _is_disabled():
        logger.info("ops_anomaly_scan: disabled via env")
        return
    interval = _interval_seconds()
    logger.info("ops_anomaly_scan: starting (interval=%ds)", interval)
    while True:
        try:
            with trace_span("cron.ops_anomaly_scan", kind="cron"):
                await run_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("ops_anomaly_scan: cycle crashed")
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise


__all__ = ["run_once", "ops_anomaly_loop"]
