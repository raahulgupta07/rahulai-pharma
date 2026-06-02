"""Stale training step reaper. Every 5 min, finds dash_training_steps rows
stuck at status='running' for >15min → marks 'failed' w/ reason 'timeout'.
Frees stuck pipelines, surfaces dead daemons. Fail-soft."""

import asyncio
import logging
import os

from sqlalchemy import text

from db.session import get_write_engine

INTERVAL_S = int(os.getenv("STALE_STEP_REAPER_INTERVAL_SECONDS", "300"))
THRESHOLD_MIN = int(os.getenv("STALE_STEP_REAPER_THRESHOLD_MIN", "15"))

log = logging.getLogger(__name__)


async def stale_step_reaper_loop():
    if os.getenv("STALE_STEP_REAPER_DISABLED", "").lower() in ("1", "true", "yes"):
        log.info("stale_step_reaper disabled via env")
        return
    while True:
        try:
            with get_write_engine().begin() as conn:
                r = conn.execute(text(f"""
                    UPDATE public.dash_training_steps
                    SET status='failed',
                        error=COALESCE(error,'') || ' [reaper:timeout >{THRESHOLD_MIN}min]',
                        updated_at=now()
                    WHERE status='running'
                      AND updated_at < now() - INTERVAL '{THRESHOLD_MIN} minutes'
                    RETURNING id
                """))
                rows = r.fetchall()
                if rows:
                    log.warning(
                        "stale_step_reaper: marked %d stuck steps as failed",
                        len(rows),
                    )
        except Exception as e:
            log.exception("stale_step_reaper tick failed: %s", e)
        await asyncio.sleep(INTERVAL_S)
