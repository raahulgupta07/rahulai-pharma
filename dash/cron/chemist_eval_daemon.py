"""Nightly clinical-eval daemon for the CityPharma chemist.

Runs the data-grounded golden eval (forward / generic / substitute / inverse)
against the chemist tools and persists accuracy into dash.dash_chemist_eval, so
the Dashboard 🧪 card shows a fresh "Clinical accuracy %" without a manual click.

Single-agent product → evaluates the locked project (LOCKED_PROJECT_SLUG,
default citypharma).

Cadence: daily (24h). Disable: CHEMIST_EVAL_DISABLED=1.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time

logger = logging.getLogger(__name__)

_DEFAULT_INTERVAL_S = 86400  # 24h


def _locked_slug() -> str:
    return os.environ.get("LOCKED_PROJECT_SLUG", "citypharma")


def _run_once() -> dict:
    from app.overview_api import run_chemist_eval
    return run_chemist_eval(_locked_slug())


async def chemist_eval_loop(interval_seconds: int = _DEFAULT_INTERVAL_S):
    """Daily background loop. Spawned by lifespan when not disabled."""
    if os.environ.get("CHEMIST_EVAL_DISABLED") == "1":
        logger.info("chemist_eval_loop disabled via CHEMIST_EVAL_DISABLED=1")
        return
    logger.info(f"chemist_eval_loop started (interval {interval_seconds}s)")
    # Stagger first run so startup isn't slammed.
    await asyncio.sleep(90)
    while True:
        try:
            t0 = time.time()
            res = await asyncio.to_thread(_run_once)
            logger.info(
                f"chemist_eval_cycle done in {int(time.time()-t0)}s: "
                f"passed={res.get('passed')} total={res.get('total')} pct={res.get('pct')}"
            )
        except Exception as e:
            logger.exception(f"chemist_eval cycle error: {e}")
        await asyncio.sleep(interval_seconds)
