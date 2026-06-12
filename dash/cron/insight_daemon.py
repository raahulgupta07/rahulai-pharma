"""Insight compilation daemon — periodic distillation of data/query insights.

Reads query history + live data, writes durable INSIGHT notes to the company
brain as `status='pending'` (admin-approved before they reach chat) and flags
stale facts for re-verification. See dash.learning.insight_curator.

DEFAULT OFF — opt in with INSIGHT_DAEMON_ENABLED=1. Hard off:
INSIGHT_DAEMON_DISABLED=1. Leader-gated at the lifespan call site. Cadence 24h,
staggered. Single-tenant → locked slug. Pure SQL, no LLM, no writes to chat.
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


async def insight_daemon_loop(interval_seconds: int = _DEFAULT_INTERVAL_S):
    if os.environ.get("INSIGHT_DAEMON_ENABLED") not in ("1", "true", "TRUE", "yes"):
        logger.info("insight_daemon_loop OFF (default) — set INSIGHT_DAEMON_ENABLED=1 to enable")
        return
    if os.environ.get("INSIGHT_DAEMON_DISABLED") in ("1", "true", "TRUE", "yes"):
        logger.info("insight_daemon_loop disabled via INSIGHT_DAEMON_DISABLED=1")
        return
    logger.info(f"insight_daemon_loop started (interval {interval_seconds}s)")
    await asyncio.sleep(180)  # stagger startup (after curator's 150s)
    from dash.learning.insight_curator import run_insight_curator
    while True:
        try:
            t0 = time.time()
            written = 0
            for slug in _slugs():
                try:
                    res = run_insight_curator(slug, dry_run=False)
                    written += res.get("written", 0)
                except Exception as e:  # noqa: BLE001
                    logger.exception(f"insight_curator crashed for {slug}: {e}")
            logger.info(f"insight_cycle done in {int(time.time()-t0)}s: written={written}")
        except Exception as e:  # noqa: BLE001
            logger.exception(f"insight cycle error: {e}")
        await asyncio.sleep(interval_seconds)
