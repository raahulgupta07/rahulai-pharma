"""Cache Curator daemon — answer-cache P3 (leader-driven auto-promotion).

Periodically pulls frequent questions (P2 clustering), asks the leader LLM to
judge which are stable + safe to pre-cache, verifies a canonical SQL answer, and
promotes it into dash.dash_answer_cache so future asks serve with zero LLM.

DEFAULT OFF — this daemon WRITES to the cache. Opt in with CACHE_CURATOR_ENABLED=1.
Also honored: CACHE_CURATOR_DISABLED=1 (hard off). Leader-gated at the lifespan
call site (same `_should_run_daemons()` as the other daemons).

Cadence: 24h, first run staggered. Single-tenant → curates the locked slug.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time

logger = logging.getLogger(__name__)

_DEFAULT_INTERVAL_S = 86400  # 24h
_MAX_PROMOTE = int(os.environ.get("CACHE_CURATOR_MAX_PROMOTE", "10") or "10")


def _slugs() -> list[str]:
    """Projects to curate. Single-tenant → just the locked slug."""
    try:
        from dash.single_agent import locked_slug
        s = locked_slug()
        return [s] if s else []
    except Exception:
        return []


async def cache_curator_loop(interval_seconds: int = _DEFAULT_INTERVAL_S):
    """Daily background loop. Spawned by lifespan when enabled + not disabled."""
    if os.environ.get("CACHE_CURATOR_ENABLED") not in ("1", "true", "TRUE", "yes"):
        logger.info("cache_curator_loop OFF (default) — set CACHE_CURATOR_ENABLED=1 to enable")
        return
    if os.environ.get("CACHE_CURATOR_DISABLED") in ("1", "true", "TRUE", "yes"):
        logger.info("cache_curator_loop disabled via CACHE_CURATOR_DISABLED=1")
        return
    logger.info(f"cache_curator_loop started (interval {interval_seconds}s, max_promote {_MAX_PROMOTE})")
    await asyncio.sleep(120)  # stagger startup
    from dash.learning.cache_curator import run_curator
    while True:
        try:
            t0 = time.time()
            promoted = skipped = 0
            for slug in _slugs():
                try:
                    res = await run_curator(slug, dry_run=False, max_promote=_MAX_PROMOTE)
                    promoted += len(res.get("promoted") or [])
                    skipped += len(res.get("skipped") or [])
                except Exception as e:  # noqa: BLE001
                    logger.exception(f"cache_curator crashed for {slug}: {e}")
            logger.info(
                f"cache_curator_cycle done in {int(time.time()-t0)}s: "
                f"promoted={promoted} skipped={skipped}"
            )
        except Exception as e:  # noqa: BLE001
            logger.exception(f"cache_curator cycle error: {e}")
        await asyncio.sleep(interval_seconds)
