"""Query-bank curator daemon — continuous query learning P3.

Periodically verifies admin-approved CANDIDATE chat patterns (re-runs their SQL
read-only) and promotes the verified, sufficiently-used ones to 'proven' (so the
Mode-1 bypass can serve them) — and demotes any whose SQL no longer returns data
or got a 👎+correction. PENDING captures are left for human review (Intern Rule).

DEFAULT OFF — this daemon mutates pattern status. Opt in with
QUERY_CURATOR_ENABLED=1. Hard off: QUERY_CURATOR_DISABLED=1. Leader-gated at the
lifespan call site. Cadence: 24h, staggered. Single-tenant → locked slug.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time

logger = logging.getLogger(__name__)

_DEFAULT_INTERVAL_S = 86400  # 24h
_MAX = int(os.environ.get("QUERY_CURATOR_MAX", "50") or "50")


def _slugs() -> list[str]:
    try:
        from dash.single_agent import locked_slug
        s = locked_slug()
        return [s] if s else []
    except Exception:
        return []


async def query_curator_loop(interval_seconds: int = _DEFAULT_INTERVAL_S):
    if os.environ.get("QUERY_CURATOR_ENABLED") not in ("1", "true", "TRUE", "yes"):
        logger.info("query_curator_loop OFF (default) — set QUERY_CURATOR_ENABLED=1 to enable")
        return
    if os.environ.get("QUERY_CURATOR_DISABLED") in ("1", "true", "TRUE", "yes"):
        logger.info("query_curator_loop disabled via QUERY_CURATOR_DISABLED=1")
        return
    logger.info(f"query_curator_loop started (interval {interval_seconds}s, max {_MAX})")
    await asyncio.sleep(150)  # stagger startup
    from dash.learning.query_curator import run_query_curator, demote_on_negative_feedback
    while True:
        try:
            t0 = time.time()
            promoted = demoted = 0
            for slug in _slugs():
                try:
                    demote_on_negative_feedback(slug)
                    res = run_query_curator(slug, limit=_MAX, dry_run=False)
                    promoted += res.get("promoted", 0)
                    demoted += res.get("demoted", 0)
                except Exception as e:  # noqa: BLE001
                    logger.exception(f"query_curator crashed for {slug}: {e}")
            logger.info(f"query_curator_cycle done in {int(time.time()-t0)}s: "
                        f"promoted={promoted} demoted={demoted}")
        except Exception as e:  # noqa: BLE001
            logger.exception(f"query_curator cycle error: {e}")
        await asyncio.sleep(interval_seconds)
