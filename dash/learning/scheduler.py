"""Daily self-learning scheduler.

Background asyncio task started at app startup. Runs once per 24h:
  1. For each project with contribute_to_central=TRUE OR receive_from_central=TRUE:
     - Run LearningCycle (project-scoped)
  2. Run central cycle (cross-project promotion)

Tracks last_run + last_error in module state for /health endpoint.
Failure-tolerant: one project failing doesn't kill the whole sweep.
Cap: max 30 minutes wall-clock per sweep.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# Observability tracing (fail-soft; no-op if unavailable or TRACING_DISABLED).
try:
    from dash.obs.trace import trace_span
except Exception:  # noqa: BLE001
    from contextlib import contextmanager as _cm

    @_cm
    def trace_span(*_a, **_k):  # type: ignore
        yield None

# Module-level state (read by /health and scheduler endpoints)
_state: dict = {
    "last_run": None,
    "last_error": None,
    "last_duration_seconds": None,
    "next_scheduled": None,
    "projects_processed": 0,
    "running": False,
    "enabled": True,
}

DAILY_INTERVAL_S = 24 * 60 * 60   # 1 day
MAX_SWEEP_S = 30 * 60             # 30 min wall-clock cap
INITIAL_DELAY_S = 60 * 60         # wait 1 hr after startup before first run
WEEKLY_CANARY_INTERVAL_S = 7 * 24 * 60 * 60  # weekly
_last_canary_day = None  # module state — date of last canary run

# Env-var opt-out: set LEARNING_SCHEDULER_DISABLED=1 to disable from boot
if os.environ.get("LEARNING_SCHEDULER_DISABLED", "").lower() in ("1", "true", "yes"):
    _state["enabled"] = False
    logger.info("learning scheduler disabled via LEARNING_SCHEDULER_DISABLED env var")

# Auto-disable when running in K8S (CronJob takes over to avoid
# multi-pod race under HPA). Override with LEARNING_SCHEDULER_FORCE_INPROCESS=1
# for single-replica or non-HPA deployments.
if os.environ.get("KUBERNETES_SERVICE_HOST"):
    if not os.environ.get("LEARNING_SCHEDULER_FORCE_INPROCESS"):
        _state["enabled"] = False
        logger.info(
            "K8S detected — in-process scheduler disabled, "
            "expecting K8S CronJob to drive cycles"
        )


def _scheduler_enabled() -> bool:
    """Read enable flag from admin settings, fall back to env/state.

    Resolution: admin_settings('enable_in_process_scheduler') →
    LEARNING_SCHEDULER_DISABLED env → in-memory _state['enabled'].
    """
    try:
        from dash.admin.settings import get_setting
        v = get_setting("enable_in_process_scheduler")
        if v is not None:
            return bool(v)
    except Exception:
        pass
    if os.environ.get("LEARNING_SCHEDULER_DISABLED", "").lower() in ("1", "true", "yes"):
        return False
    return bool(_state.get("enabled", True))


def get_state() -> dict:
    """Return a snapshot of scheduler state."""
    return dict(_state)


def disable() -> None:
    _state["enabled"] = False


def enable() -> None:
    _state["enabled"] = True


# ---------------------------------------------------------------------------
# Core sweep helpers
# ---------------------------------------------------------------------------

async def _run_one_project(slug: str, llm_call_fn=None) -> None:
    """Run a project-scoped LearningCycle. Failure-tolerant."""
    try:
        from dash.learning.cycle import LearningCycle
        try:
            from dash.settings import training_llm_call
        except Exception:
            training_llm_call = None  # type: ignore

        cyc = LearningCycle(
            project_slug=slug,
            llm_call_fn=llm_call_fn or training_llm_call,
            max_questions=15,        # smaller cap in cron mode
            run_decay=False,         # decay only once per sweep, central
            run_promotion=False,     # promotion only on central pass
        )
        async for _ev in cyc.run():
            pass  # discard SSE events; persisted in dash_self_learning_runs
    except Exception as e:
        logger.warning(f"cron cycle for {slug} failed: {e}")


async def _run_central(llm_call_fn=None) -> None:
    """Run the central (cross-project) LearningCycle. Failure-tolerant."""
    try:
        from dash.learning.cycle import LearningCycle
        try:
            from dash.settings import training_llm_call
        except Exception:
            training_llm_call = None  # type: ignore

        cyc = LearningCycle(
            project_slug=None,           # central
            llm_call_fn=llm_call_fn or training_llm_call,
            max_questions=10,
            run_decay=True,              # decay only on central pass
            run_promotion=True,
        )
        async for _ev in cyc.run():
            pass
    except Exception as e:
        logger.warning(f"central cron cycle failed: {e}")


def _get_optin_projects() -> list[str]:
    """Read dash_projects where contribute_to_central or receive_from_central."""
    try:
        from sqlalchemy import text
        from db.session import get_sql_engine
        with get_sql_engine().connect() as conn:
            rows = conn.execute(text(
                "SELECT slug FROM public.dash_projects "
                "WHERE COALESCE(contribute_to_central, TRUE) = TRUE "
                "   OR COALESCE(receive_from_central, TRUE) = TRUE "
                "ORDER BY slug"
            )).fetchall()
        return [r[0] for r in rows]
    except Exception as e:
        logger.warning(f"_get_optin_projects: {e}")
        return []


async def _maybe_run_canary() -> None:
    """Once per week (Sunday UTC), run a dry-run cycle on each project.

    Detects regressions in the heuristic layer at $0 cost (no LLM calls).
    """
    global _last_canary_day
    today = datetime.utcnow().date()
    if _last_canary_day == today:
        return  # already ran today
    # Only Sundays (weekday 6)
    if today.weekday() != 6:
        return
    _last_canary_day = today

    logger.info("running weekly dry-run canary")
    slugs = await asyncio.to_thread(_get_optin_projects)
    for slug in slugs:
        try:
            from dash.learning.cycle import LearningCycle
            cyc = LearningCycle(
                project_slug=slug, dry_run=True,
                max_questions=5, run_decay=False, run_promotion=False,
            )
            async for _ev in cyc.run():
                pass
        except Exception as e:
            logger.warning(f"canary {slug} failed: {e}")


async def _maybe_run_drift_scan() -> None:
    """Run per-source drift detection across all active sources.

    Honors admin setting ``enable_self_learning`` — when disabled globally,
    drift scanning is turned off in lockstep with the learning loop.
    """
    try:
        # Admin global kill-switch (shares the self-learning toggle)
        try:
            from dash.admin.settings import get_setting
            v = get_setting("enable_self_learning")
            if v is not None and not bool(v):
                logger.info("drift scan skipped (enable_self_learning=False)")
                return
        except Exception:
            pass
        from dash.learning.drift_detector import detect_for_source
        from sqlalchemy import text
        from db.session import get_sql_engine
        with get_sql_engine().connect() as conn:
            rows = conn.execute(text(
                "SELECT id, project_slug FROM public.dash_data_sources "
                "WHERE status = 'active' AND last_trained_at IS NOT NULL"
            )).fetchall()
        for sid, slug in rows:
            try:
                await asyncio.to_thread(detect_for_source, slug, sid)
            except Exception as e:
                logger.debug(f"drift scan {slug}/{sid}: {e}")
    except Exception as e:
        logger.warning(f"drift scan sweep: {e}")


async def _sweep_once() -> None:
    """Run one full sweep: every opt-in project, then central."""
    # If in-memory flag is explicitly disabled, honor it (tests + disable() API).
    # Otherwise consult admin settings for runtime override.
    if _state.get("enabled", True):
        try:
            if not _scheduler_enabled():
                _state["enabled"] = False
        except Exception:
            pass
    if not _state["enabled"]:
        logger.info("scheduler disabled, skipping sweep")
        return

    if _state["running"]:
        logger.info("sweep already in progress, skipping")
        return

    _state["running"] = True
    _state["projects_processed"] = 0
    t0 = time.time()

    with trace_span("cron.learning_scheduler", kind="cron"):
      try:
        # 1. Get list of opt-in projects (DB call wrapped in to_thread)
        slugs = await asyncio.to_thread(_get_optin_projects)
        logger.info(f"scheduler sweep starting: {len(slugs)} projects")

        # 2. Per-project cycles, sequential, with wall-clock cap
        for slug in slugs:
            if time.time() - t0 > MAX_SWEEP_S:
                logger.warning("sweep exceeded MAX_SWEEP_S; stopping early")
                break
            await _run_one_project(slug)
            _state["projects_processed"] += 1

        # 3. Central cycle (decay + promotion)
        await _run_central()

        # 4. Weekly dry-run canary (Sunday UTC only) — regression check
        try:
            await _maybe_run_canary()
        except Exception as e:
            logger.warning(f"canary sweep failed: {e}")

        # 5. Per-source drift scan (admin can disable globally
        #    via admin_settings.enable_self_learning)
        try:
            await _maybe_run_drift_scan()
        except Exception as e:
            logger.warning(f"drift scan failed: {e}")

        _state["last_run"] = datetime.utcnow().isoformat()
        _state["last_duration_seconds"] = int(time.time() - t0)
        _state["last_error"] = None
        _state["next_scheduled"] = (
            datetime.utcnow() + timedelta(seconds=DAILY_INTERVAL_S)
        ).isoformat()
        logger.info(
            f"scheduler sweep done: {_state['projects_processed']} projects "
            f"in {_state['last_duration_seconds']}s"
        )
      except Exception as e:
        logger.exception(f"sweep failed: {e}")
        _state["last_error"] = str(e)[:300]
      finally:
        _state["running"] = False


# ---------------------------------------------------------------------------
# Async daemon loop
# ---------------------------------------------------------------------------

async def scheduler_loop() -> None:
    """Async daemon — sleeps INITIAL_DELAY_S, then loops every DAILY_INTERVAL_S."""
    logger.info(f"learning scheduler starting (initial delay {INITIAL_DELAY_S}s)")
    try:
        await asyncio.sleep(INITIAL_DELAY_S)
    except asyncio.CancelledError:
        return
    while True:
        if _state["enabled"]:
            try:
                await _sweep_once()
            except Exception as e:
                logger.exception(f"scheduler_loop iteration failed: {e}")
        # sleep regardless (so health stays consistent)
        try:
            await asyncio.sleep(DAILY_INTERVAL_S)
        except asyncio.CancelledError:
            return


_task: Optional[asyncio.Task] = None


def start_scheduler() -> Optional[asyncio.Task]:
    """Called from FastAPI startup. Idempotent."""
    global _task
    if _task is not None and not _task.done():
        return _task
    try:
        loop = asyncio.get_event_loop()
        _task = loop.create_task(scheduler_loop())
        logger.info("learning scheduler task started")
        return _task
    except RuntimeError as e:
        logger.warning(f"start_scheduler failed (no loop?): {e}")
        return None


async def trigger_now() -> dict:
    """Force a sweep now (for /api/learning/scheduler/trigger endpoint).

    Returns state snapshot.
    """
    if not _state["running"]:
        asyncio.create_task(_sweep_once())
    return get_state()
