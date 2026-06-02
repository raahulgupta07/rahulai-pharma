"""Hourly refresh of per-table usage stats + mv_table_usage.

Mirrors the pattern in ontology_cluster_daemon.py: an async loop that
honors `DAEMONS_DISABLED=1`, `K8S_DAEMON_MODE=cronjob`, and the
daemon-specific kill switch. Calls `refresh_table_usage()` which parses
dash_traces.meta->>'sql' via sqlglot and writes
public.dash_table_usage_stats + REFRESH MATERIALIZED VIEW mv_table_usage.

Fail-soft: cycle crashes are caught + logged, the loop never dies.
"""
from __future__ import annotations

import asyncio
import logging
import os

logger = logging.getLogger(__name__)

# Observability tracing — fail-soft no-op if unavailable.
try:
    from dash.obs.trace import trace_span
except Exception:  # noqa: BLE001
    from contextlib import contextmanager as _cm

    @_cm
    def trace_span(*_a, **_k):  # type: ignore
        yield None


DEFAULT_INTERVAL_SECONDS = 60 * 60  # 1h


def _is_disabled() -> bool:
    return os.getenv("TABLE_USAGE_REFRESH_DISABLED", "").lower() in (
        "1", "true", "yes",
    )


def _interval_seconds() -> int:
    raw = os.getenv("TABLE_USAGE_REFRESH_INTERVAL_SECONDS", "")
    try:
        v = int(raw)
        if v > 0:
            return v
    except Exception:
        pass
    return DEFAULT_INTERVAL_SECONDS


def run_cycle() -> dict:
    """Sync refresh — runs in a thread to keep the event loop unblocked."""
    try:
        from app.table_usage_api import refresh_table_usage
    except Exception as e:
        logger.warning("table_usage_refresh: import failed: %s", e)
        return {"errors": 1, "error": str(e)}
    try:
        return refresh_table_usage()
    except Exception as e:
        logger.exception("table_usage_refresh: cycle crashed")
        return {"errors": 1, "error": str(e)}


async def table_usage_refresh_loop() -> None:
    if _is_disabled():
        logger.info("table_usage_refresh: disabled via env")
        return
    interval = _interval_seconds()
    logger.info("table_usage_refresh: starting (interval=%ds)", interval)
    while True:
        try:
            with trace_span("cron.table_usage_refresh", kind="cron"):
                stats = await asyncio.to_thread(run_cycle)
            logger.info(
                "table_usage_refresh: cycle_done traces=%d rows=%d errors=%d",
                stats.get("traces_scanned", 0),
                stats.get("rows_written", 0),
                stats.get("errors", 0),
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("table_usage_refresh: outer loop crashed")
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise


__all__ = ["run_cycle", "table_usage_refresh_loop"]
