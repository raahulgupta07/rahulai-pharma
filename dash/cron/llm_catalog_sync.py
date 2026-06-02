"""Daily 03:00 UTC sync of OpenRouter model catalog.

Mirror of dash/cron/auto_campaign_daemon.py structure.
Gates: LLM_CATALOG_SYNC_DISABLED=1 env, plus master _should_run_daemons()
in app/main.py lifespan when wired.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _is_disabled() -> bool:
    return os.getenv("LLM_CATALOG_SYNC_DISABLED", "").lower() in ("1", "true", "yes")


def _seconds_until_next_3am_utc() -> float:
    now = datetime.now(timezone.utc)
    target = now.replace(hour=3, minute=0, second=0, microsecond=0)
    if target <= now:
        # next day
        from datetime import timedelta
        target = target + timedelta(days=1)
    return max(60.0, (target - now).total_seconds())


async def llm_catalog_sync_loop() -> None:
    """Forever-loop: sleep until next 03:00 UTC, sync, repeat."""
    if _is_disabled():
        logger.info("llm_catalog_sync: disabled via env")
        return
    logger.info("llm_catalog_sync: starting (daily 03:00 UTC)")
    # initial sync on boot if catalog empty — non-blocking
    try:
        from dash.admin.llm_catalog import get_sync_status, sync_catalog
        status = await asyncio.to_thread(get_sync_status)
        if int(status.get("count") or 0) == 0:
            logger.info("llm_catalog_sync: empty catalog, doing initial sync")
            await asyncio.to_thread(sync_catalog)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("llm_catalog_sync: initial sync failed")

    while True:
        sleep_s = _seconds_until_next_3am_utc()
        logger.info("llm_catalog_sync: sleeping %.0fs until next 03:00 UTC", sleep_s)
        try:
            await asyncio.sleep(sleep_s)
        except asyncio.CancelledError:
            raise
        try:
            from dash.admin.llm_catalog import sync_catalog
            res = await asyncio.to_thread(sync_catalog)
            logger.info("llm_catalog_sync: cycle done count=%s", res.get("count"))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("llm_catalog_sync: cycle failed")


__all__ = ["llm_catalog_sync_loop"]
