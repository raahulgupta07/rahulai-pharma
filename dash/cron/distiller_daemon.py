"""Self-distill daemon (#5) — periodically distil pending memory facts from
recent 👎 corrections (dash.learning.distiller).

DEFAULT OFF — opt in with DISTILLER_ENABLED=1. Hard off: DISTILLER_DISABLED=1.
Leader-gated at the lifespan call site. Cadence 24h, staggered. Reads only
already-captured feedback; the only cost is the capped LITE-model extraction.
Writes pending memories (admin-approved before chat).
"""
from __future__ import annotations

import asyncio
import logging
import os
import time

logger = logging.getLogger(__name__)

_DEFAULT_INTERVAL_S = 86400  # 24h


def _slugs() -> list[str]:
    try:
        from dash.single_agent import locked_slug
        s = locked_slug()
        return [s] if s else []
    except Exception:
        return []


async def distiller_loop(interval_seconds: int = _DEFAULT_INTERVAL_S):
    if os.environ.get("DISTILLER_ENABLED") not in ("1", "true", "TRUE", "yes"):
        logger.info("distiller_loop OFF (default) — set DISTILLER_ENABLED=1 to enable")
        return
    if os.environ.get("DISTILLER_DISABLED") in ("1", "true", "TRUE", "yes"):
        logger.info("distiller_loop disabled via DISTILLER_DISABLED=1")
        return
    logger.info(f"distiller_loop started (interval {interval_seconds}s)")
    await asyncio.sleep(210)  # stagger (after insight daemon's 180s)
    from dash.learning.distiller import run_distiller
    while True:
        try:
            t0 = time.time()
            written = 0
            for slug in _slugs():
                try:
                    # Off the event loop — blocking SQL+LLM; leader is also a chat worker.
                    res = await asyncio.to_thread(run_distiller, slug, False)
                    written += res.get("written", 0)
                except Exception as e:  # noqa: BLE001
                    logger.exception(f"distiller crashed for {slug}: {e}")
            logger.info(f"distiller_cycle done in {int(time.time()-t0)}s: written={written}")
        except Exception as e:  # noqa: BLE001
            logger.exception(f"distiller cycle error: {e}")
        await asyncio.sleep(interval_seconds)
