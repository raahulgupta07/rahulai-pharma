"""Phase 7 — Deck schedule cron runner.

Polls public.dash_deck_schedules every 60s. For each enabled schedule
whose cron expression is due (relative to last_run_at), invokes
dash.distribution.delivery.deliver_scheduled_deck() in a worker thread.

deliver_scheduled_deck already persists last_run_at/last_status/last_error
via its internal _record_run() helper, so this loop only needs to drive
the schedule (avoid double-firing) and update on failure paths where
delivery itself never ran.

Disable via:
  DECK_SCHEDULE_DAEMON_DISABLED=1
  DAEMONS_DISABLED=1
  K8S_DAEMON_MODE=cronjob
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
from typing import Any, Optional

log = logging.getLogger(__name__)

POLL_INTERVAL_S = int(os.getenv("DECK_SCHEDULE_POLL_S", "60"))


def _disabled() -> bool:
    if os.getenv("DECK_SCHEDULE_DAEMON_DISABLED") in ("1", "true", "TRUE", "yes"):
        return True
    if os.getenv("DAEMONS_DISABLED") in ("1", "true", "TRUE", "yes"):
        return True
    if os.getenv("K8S_DAEMON_MODE") == "cronjob":
        return True
    return False


def _get_engine():
    try:
        from db.session import get_write_engine
        return get_write_engine()
    except Exception:
        try:
            from db.session import get_sql_engine  # last-resort read engine
            return get_sql_engine()
        except Exception:
            return None


def _compute_next_run(cron_expr: str, last_run: Optional[dt.datetime]) -> Optional[dt.datetime]:
    """Compute next-fire timestamp from cron + last_run.

    Prefers `croniter` (installed). Falls back to "fire every hour" if cron
    parsing fails — safer than never firing.
    """
    base = last_run or (dt.datetime.utcnow() - dt.timedelta(days=1))
    try:
        from croniter import croniter
        if not croniter.is_valid(cron_expr):
            log.warning("invalid cron expr %r; defaulting to hourly", cron_expr)
            return base + dt.timedelta(hours=1)
        return croniter(cron_expr, base).get_next(dt.datetime)
    except Exception as e:
        log.warning("croniter unavailable or failed (%s); defaulting to hourly", e)
        return base + dt.timedelta(hours=1)


async def _fetch_schedules() -> list[dict[str, Any]]:
    eng = _get_engine()
    if eng is None:
        return []
    try:
        from sqlalchemy import text
        with eng.connect() as conn:
            rows = conn.execute(text(
                """
                SELECT id, project_slug, presentation_id, name, cron, recipients,
                       channel, format, enabled, last_run_at, last_status, last_error
                FROM public.dash_deck_schedules
                WHERE enabled = TRUE
                """
            )).mappings().all()
        return [dict(r) for r in rows]
    except Exception as e:
        log.warning("fetch_schedules failed: %s", e)
        return []


def _run_delivery_sync(sched: dict[str, Any]) -> None:
    """Invoke delivery + handle catastrophic failure (delivery raised).

    `deliver_scheduled_deck` records normal success/error itself. We only
    need to backstop unexpected exceptions so last_status reflects them.
    """
    sid = sched.get("id")
    pid = sched.get("presentation_id")
    try:
        from dash.distribution.delivery import deliver_scheduled_deck
        res = deliver_scheduled_deck(pid, sched)
        log.info("deck_schedule id=%s pid=%s -> %s", sid, pid, res.get("status") if isinstance(res, dict) else "?")
    except Exception as exc:
        log.exception("deck_schedule id=%s delivery raised", sid)
        # Mark as error since delivery never recorded.
        try:
            from sqlalchemy import text
            eng = _get_engine()
            if eng is None:
                return
            with eng.connect() as conn:
                conn.execute(text(
                    "UPDATE public.dash_deck_schedules "
                    "SET last_run_at = :ts, last_status = 'error', last_error = :err "
                    "WHERE id = :id"
                ), {
                    "ts": dt.datetime.utcnow(),
                    "err": str(exc)[:2000],
                    "id": sid,
                })
                conn.commit()
        except Exception as inner:
            log.warning("deck_schedule id=%s error-update failed: %s", sid, inner)


async def _tick() -> None:
    schedules = await _fetch_schedules()
    if not schedules:
        return
    now = dt.datetime.utcnow()
    for sched in schedules:
        cron = sched.get("cron") or ""
        if not cron:
            continue
        last_run = sched.get("last_run_at")
        # last_run_at may be timestamptz (aware) — strip to naive UTC to match the
        # naive `now` above, else `now - last_run` raises TypeError.
        if last_run is not None and getattr(last_run, "tzinfo", None) is not None:
            last_run = last_run.replace(tzinfo=None)
        # Idempotency: skip if already fired within same minute window.
        if last_run and (now - last_run).total_seconds() < 60:
            continue
        next_run = _compute_next_run(cron, last_run)
        if next_run is None or now < next_run:
            continue
        # Due — fire in thread (don't block loop).
        try:
            asyncio.create_task(asyncio.to_thread(_run_delivery_sync, sched))
        except Exception as e:
            log.warning("deck_schedule id=%s dispatch failed: %s", sched.get("id"), e)


async def deck_schedule_loop() -> None:
    if _disabled():
        log.info("deck_schedule_runner disabled")
        return
    log.info("deck_schedule_runner starting (poll=%ds)", POLL_INTERVAL_S)
    while True:
        try:
            await _tick()
        except Exception as e:
            log.warning("deck_schedule_loop iteration failed: %s", e)
        await asyncio.sleep(POLL_INTERVAL_S)
