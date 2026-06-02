"""Weekly benchmark sync loop.

Runs ``benchmark_sync.sync_benchmarks()`` every
``BENCHMARK_SYNC_INTERVAL_SECONDS`` (default 7 days). Wired into
``app/main.py`` lifespan as a guarded ``asyncio.create_task`` and also callable
from a K8s CronJob via ``POST /api/ontology/benchmarks/sync-now``.

Disable in-process via ``BENCHMARK_SYNC_DISABLED=1`` (e.g. when running as
a dedicated cron pod).
"""
from __future__ import annotations

import asyncio
import logging
import os

logger = logging.getLogger(__name__)

# Observability tracing (fail-soft; no-op if unavailable or TRACING_DISABLED).
try:
    from dash.obs.trace import trace_span
except Exception:  # noqa: BLE001
    from contextlib import contextmanager as _cm

    @_cm
    def trace_span(*_a, **_k):  # type: ignore
        yield None

DEFAULT_INTERVAL_SECONDS: int = 7 * 24 * 60 * 60  # 7 days


def _interval() -> int:
    try:
        return int(os.environ.get(
            "BENCHMARK_SYNC_INTERVAL_SECONDS", DEFAULT_INTERVAL_SECONDS,
        ))
    except (TypeError, ValueError):
        return DEFAULT_INTERVAL_SECONDS


async def benchmark_sync_loop() -> None:
    """Forever-loop: run benchmark sync, sleep ``interval``, repeat.

    Cancellation-safe (asyncio.CancelledError is propagated). All other
    exceptions are caught + logged so a transient error never kills the loop.
    """
    if os.environ.get("BENCHMARK_SYNC_DISABLED") in ("1", "true", "TRUE", "yes"):
        logger.info("benchmark_sync_loop: disabled via env, exiting")
        return

    interval = max(60, _interval())  # clamp to >= 60s for safety
    logger.info(f"benchmark_sync_loop: starting (interval={interval}s)")

    # Stagger first run by 5 minutes so app boot isn't slowed.
    try:
        await asyncio.sleep(300)
    except asyncio.CancelledError:
        raise

    while True:
        try:
            from dash.learning.benchmark_sync import sync_benchmarks
            with trace_span("cron.benchmark_sync", kind="cron"):
                stats = await sync_benchmarks()
            logger.info(f"benchmark_sync_loop: cycle complete: {stats}")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("benchmark_sync_loop: cycle failed")
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise


__all__ = ["benchmark_sync_loop"]
