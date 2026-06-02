"""Connector sync scheduler.

APScheduler-based background runner that drives automatic syncs for
``dash_data_sources`` rows whose ``sync_schedule`` is non-manual.

Design notes
------------
- Single tick every 60s named ``connector_sync_tick``.
- Uses ``pg_try_advisory_lock(hashtext('connector:'||id))`` so under
  multi-worker uvicorn (WORKERS=4+) only one worker fires each due
  source per tick. The lock is released in a ``finally`` block.
- Schedule mapping: hourly=1h, daily=24h, weekly=7d. Anything else
  (including ``manual``) is ignored.
- Never crashes the scheduler thread — every block wraps in try/except
  and logs to stderr via ``logging``.
- Opt-out: set ``CONNECTOR_SCHEDULER_DISABLED=1`` (also auto-disabled
  under ``KUBERNETES_SERVICE_HOST`` unless ``CONNECTOR_SCHEDULER_FORCE_INPROCESS=1``).
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Optional

from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from db import db_url

logger = logging.getLogger(__name__)

_SCHEDULE_INTERVAL_HOURS = {"hourly": 1, "daily": 24, "weekly": 24 * 7}

_engine = create_engine(db_url, poolclass=NullPool)
_scheduler = None  # type: ignore[var-annotated]
_state: dict = {"last_tick": None, "last_error": None, "fired": 0, "running": False}


def _is_disabled() -> bool:
    if os.environ.get("CONNECTOR_SCHEDULER_DISABLED", "").lower() in ("1", "true", "yes"):
        return True
    if os.environ.get("KUBERNETES_SERVICE_HOST") and not os.environ.get(
        "CONNECTOR_SCHEDULER_FORCE_INPROCESS"
    ):
        return True
    return False


def _audit(action: str, source_id: int, project_slug: Optional[str], detail: str) -> None:
    """Insert a row into dash_audit_log. Never raises."""
    try:
        with _engine.connect() as conn:
            conn.execute(text(
                "INSERT INTO public.dash_audit_log "
                "(username, action, resource_type, resource_id, details, project_slug) "
                "VALUES ('scheduler', :a, 'data_source', :rid, :d, :p)"
            ), {"a": action, "rid": str(source_id), "d": detail[:1000], "p": project_slug})
            conn.commit()
    except Exception as e:
        logger.warning(f"audit insert failed: {e}")


def _due_sources() -> list[tuple[int, str, str, list[str]]]:
    """Return [(id, project_slug, sync_schedule, tables), ...] for due sources."""
    rows: list = []
    try:
        with _engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT id, project_slug, sync_schedule, config, last_sync_at, "
                "       EXTRACT(EPOCH FROM (NOW() - COALESCE(last_sync_at, "
                "          NOW() - INTERVAL '100 years'))) AS age_s "
                "FROM public.dash_data_sources "
                "WHERE status = 'active' "
                "  AND sync_schedule IS NOT NULL "
                "  AND sync_schedule <> 'manual' "
                "  AND source_type IN ('postgresql','mysql','fabric')"
            )).fetchall()
    except Exception as e:
        logger.warning(f"_due_sources query failed: {e}")
        return []

    out: list[tuple[int, str, str, list[str]]] = []
    for r in rows:
        sid, slug, sched, config_raw, _last, age_s = r
        sched_norm = (sched or "").lower().strip()
        hours = _SCHEDULE_INTERVAL_HOURS.get(sched_norm)
        if not hours:
            continue
        if age_s is None:
            continue
        if float(age_s) < hours * 3600:
            continue  # not due yet

        # Extract tables from config
        cfg = config_raw if isinstance(config_raw, dict) else {}
        tables = list(cfg.get("selected_tables") or [])
        out.append((sid, slug or "", sched_norm, tables))
    return out


def _run_one(source_id: int, project_slug: str, sched: str, tables: list[str]) -> None:
    """Run a single sync under an advisory lock. Always release lock."""
    # hashtext() returns int4; pg_try_advisory_lock(bigint) accepts via implicit cast.
    lock_sql = "SELECT pg_try_advisory_lock(hashtext('connector:' || :sid)::bigint)"
    unlock_sql = "SELECT pg_advisory_unlock(hashtext('connector:' || :sid)::bigint)"
    got_lock = False
    conn = None
    try:
        conn = _engine.connect()
        got_lock = bool(conn.execute(text(lock_sql), {"sid": str(source_id)}).scalar())
        if not got_lock:
            logger.debug(f"connector-sched: lock busy for source={source_id}")
            return

        logger.info(f"connector-sched: firing source={source_id} schedule={sched}")
        _audit("connector_sync_fire", source_id, project_slug,
               f"schedule={sched} tables={len(tables)}")

        # Run sync_worker in its own thread so a long sync doesn't block
        # the APScheduler worker pool. We still wait via .join with a cap.
        from app.connectors import sync_worker
        result_box: dict = {}

        def _runner():
            try:
                result_box["result"] = sync_worker(source_id, tables, force=False)
            except Exception as e:
                result_box["result"] = {"ok": False, "error": str(e)[:300]}

        t = threading.Thread(target=_runner, daemon=True, name=f"sync-{source_id}")
        t.start()
        # Cap an individual sync at 30 minutes to avoid wedging the lock.
        t.join(timeout=30 * 60)

        result = result_box.get("result", {"ok": False, "error": "sync timed out"})
        _state["fired"] += 1
        _audit(
            "connector_sync_done" if result.get("ok") else "connector_sync_error",
            source_id, project_slug,
            f"synced={result.get('synced', 0)} skipped={result.get('skipped', 0)} "
            f"errors={result.get('errors', 0)} err={result.get('error', '')}",
        )
    except Exception as e:
        logger.exception(f"connector-sched: source {source_id} crashed: {e}")
        try:
            _audit("connector_sync_error", source_id, project_slug, f"crash: {str(e)[:300]}")
        except Exception:
            pass
    finally:
        if conn is not None:
            try:
                if got_lock:
                    conn.execute(text(unlock_sql), {"sid": str(source_id)})
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass


def _tick() -> None:
    """One scheduler tick — dispatch all due sources."""
    if _state.get("running"):
        # Overlapping ticks are fine in principle (advisory lock prevents
        # double-fire) but skipping cuts contention.
        return
    _state["running"] = True
    try:
        due = _due_sources()
        if due:
            logger.info(f"connector-sched tick: {len(due)} due source(s)")
        for sid, slug, sched, tables in due:
            try:
                _run_one(sid, slug, sched, tables)
            except Exception as e:
                logger.warning(f"connector-sched: _run_one({sid}) failed: {e}")
        from datetime import datetime
        _state["last_tick"] = datetime.utcnow().isoformat()
        _state["last_error"] = None
    except Exception as e:
        logger.exception("connector-sched tick crashed")
        _state["last_error"] = str(e)[:300]
    finally:
        _state["running"] = False


def get_state() -> dict:
    return dict(_state)


def start_connector_scheduler() -> Optional[object]:
    """Idempotently start the APScheduler BackgroundScheduler."""
    global _scheduler
    if _is_disabled():
        logger.info("connector scheduler disabled via env")
        return None
    if _scheduler is not None:
        return _scheduler
    try:
        from apscheduler.schedulers.background import BackgroundScheduler  # type: ignore
    except Exception as e:
        logger.warning(f"APScheduler not installed, connector scheduler not started: {e}")
        return None

    try:
        sch = BackgroundScheduler(timezone="UTC", daemon=True)
        sch.add_job(
            _tick,
            "interval",
            seconds=60,
            id="connector_sync_tick",
            name="connector_sync_tick",
            max_instances=1,
            coalesce=True,
            replace_existing=True,
        )
        sch.start()
        _scheduler = sch
        logger.info("connector scheduler started (60s tick)")
        return sch
    except Exception as e:
        logger.exception(f"failed to start connector scheduler: {e}")
        return None
