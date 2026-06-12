"""S3 auto-sync daemon — polls due S3 sources and runs the sync pipeline.

DEFAULT OFF — opt in with S3_SYNC_ENABLED=1. Hard off: S3_SYNC_DISABLED=1.
Leader-gated (spawned by the single daemon leader in app/main.py lifespan).

Each tick: find enabled sources whose schedule interval elapsed → run_s3_sync.
Per-object ETag change-detection means an unchanged bucket is a cheap no-op.
"""
from __future__ import annotations

import asyncio
import logging
import os

logger = logging.getLogger(__name__)

# How often the daemon wakes to check which sources are due (NOT the per-source
# schedule — that is schedule_seconds on each source row). 60s default.
_TICK_SECONDS = int(os.getenv("S3_SYNC_TICK_SECONDS", "60"))


def _enabled() -> bool:
    if os.getenv("S3_SYNC_DISABLED") in ("1", "true", "TRUE", "yes"):
        return False
    return os.getenv("S3_SYNC_ENABLED") in ("1", "true", "TRUE", "yes")


async def s3_sync_loop(tick_seconds: int | None = None) -> None:
    tick = tick_seconds or _TICK_SECONDS
    logger.info("s3_sync daemon started (tick=%ss)", tick)
    while True:
        try:
            if _enabled():
                from app.s3_sync import due_source_ids, run_s3_sync
                ids = due_source_ids()
                for sid in ids:
                    try:
                        # run blocking sync off the event loop
                        res = await asyncio.to_thread(run_s3_sync, sid, False, "daemon")
                        if res.get("changed"):
                            logger.info("s3_sync: source %s synced %s object(s)", sid, res.get("changed"))
                    except Exception:
                        logger.exception("s3_sync: source %s failed", sid)
        except Exception:
            logger.exception("s3_sync loop tick failed")
        await asyncio.sleep(tick)
