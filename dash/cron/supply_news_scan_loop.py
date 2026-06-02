"""
Supply News Scan daemon — every 7d, run news_scan_suppliers (stub for now).

Future: this loop will embed news. For now it just calls the stub so the
cron schedule and trace plumbing are in place.

Tunables:
  SUPPLY_NEWS_DAEMON_DISABLED=1   → skip
  SUPPLY_NEWS_INTERVAL_SECONDS    → default 604800 (7d), floor 3600
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


def _is_disabled() -> bool:
    return (os.getenv("SUPPLY_NEWS_DAEMON_DISABLED", "").lower()
            in ("1", "true", "yes"))


def _interval_seconds() -> int:
    raw = os.getenv("SUPPLY_NEWS_INTERVAL_SECONDS", "")
    try:
        v = int(raw)
    except Exception:
        v = DEFAULT_INTERVAL_SECONDS
    if v < MIN_INTERVAL_SECONDS:
        v = MIN_INTERVAL_SECONDS
    return v


async def run_once() -> dict[str, Any]:
    try:
        from dash.tools.supply_tools import news_scan_suppliers
    except Exception as e:
        logger.exception("supply_news: import failed")
        return {"ok": False, "error": f"import_failed: {e}"}
    try:
        events = await asyncio.to_thread(news_scan_suppliers, None, 24 * 7)
        logger.info("supply_news: cycle_done events=%d", len(events))
        return {"ok": True, "events_count": len(events)}
    except Exception as e:  # noqa: BLE001
        logger.exception("supply_news: scan crashed")
        return {"ok": False, "error": str(e)}


async def supply_news_scan_loop() -> None:
    if _is_disabled():
        logger.info("supply_news: disabled via env")
        return
    interval = _interval_seconds()
    logger.info("supply_news: starting (interval=%ds)", interval)
    while True:
        try:
            with trace_span("cron.supply_news_scan", kind="cron"):
                await run_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("supply_news: cycle crashed")
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise


__all__ = ["run_once", "supply_news_scan_loop"]
