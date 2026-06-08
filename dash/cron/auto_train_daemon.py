"""
Auto-train daemon — watches for data changes and enqueues retraining.
Checks every AUTO_TRAIN_POLL_INTERVAL_S (default 900 = 15 min).
Disable: AUTO_TRAIN_DAEMON_DISABLED=1
"""
import asyncio
import logging
import os
import time
from typing import Optional

log = logging.getLogger("dash.auto_train")

_POLL_INTERVAL = int(os.getenv("AUTO_TRAIN_POLL_INTERVAL_S", "900"))
_DISABLED = os.getenv("AUTO_TRAIN_DAEMON_DISABLED", "0").strip().lower() in ("1", "true", "yes")
_ROW_DELTA_THRESHOLD = float(os.getenv("AUTO_TRAIN_ROW_DELTA_PCT", "5"))  # 5%
# Don't fire an auto-train if a run started within this window — an upload's
# promote+train is likely already handling these tables (its metadata write lags
# the table load, so the untrained-check would otherwise double-fire). 10 min.
_RECENT_RUN_COOLDOWN_S = int(os.getenv("AUTO_TRAIN_RECENT_RUN_COOLDOWN_S", "600"))


async def _seconds_since_last_run(slug: str) -> float | None:
    """Seconds since the most recent training run STARTED (any status), or None."""
    try:
        from db.session import get_sql_engine
        from sqlalchemy import text
        eng = get_sql_engine()
        with eng.connect() as conn:
            row = conn.execute(text(
                "SELECT EXTRACT(EPOCH FROM (now() - max(started_at))) "
                "FROM public.dash_training_runs WHERE project_slug = :s"
            ), {"s": slug}).fetchone()
        return float(row[0]) if row and row[0] is not None else None
    except Exception:
        return None

# In-memory state: last known row counts per project
_last_row_counts: dict[str, dict[str, int]] = {}  # {slug: {table: count}}
_last_trained_at: dict[str, float] = {}  # {slug: epoch}


async def _get_locked_slug() -> Optional[str]:
    try:
        from dash.single_agent import locked_slug, is_single_agent
        if is_single_agent():
            return locked_slug()
    except Exception:
        pass
    return None


async def _get_current_row_counts(slug: str) -> dict[str, int]:
    """Query information_schema for current row estimates."""
    try:
        from db.session import get_sql_engine
        from sqlalchemy import text
        # Use pg_stat_user_tables for fast row count estimate
        eng = get_sql_engine()
        with eng.connect() as conn:
            rows = conn.execute(text(
                "SELECT relname, n_live_tup FROM pg_stat_user_tables "
                "WHERE schemaname = :s ORDER BY relname"
            ), {"s": slug}).fetchall()
        return {r[0]: int(r[1]) for r in rows if r[1] >= 0}
    except Exception as e:
        log.debug(f"auto_train: row count query failed for {slug}: {e}")
        return {}


async def _get_last_trained_epoch(slug: str) -> float:
    """Get epoch of last completed training run."""
    try:
        from db.session import get_sql_engine
        from sqlalchemy import text
        eng = get_sql_engine()
        with eng.connect() as conn:
            row = conn.execute(text(
                "SELECT EXTRACT(EPOCH FROM finished_at) FROM public.dash_training_runs "
                "WHERE project_slug = :s AND status = 'done' "
                "ORDER BY finished_at DESC LIMIT 1"
            ), {"s": slug}).fetchone()
        return float(row[0]) if row and row[0] else 0.0
    except Exception:
        return 0.0


async def _is_training_running(slug: str) -> bool:
    """Return True if a training job is currently queued or running."""
    try:
        from db.session import get_sql_engine
        from sqlalchemy import text
        eng = get_sql_engine()
        with eng.connect() as conn:
            row = conn.execute(text(
                "SELECT COUNT(*) FROM public.dash_training_runs "
                "WHERE project_slug = :s AND status IN ('running', 'queued')"
            ), {"s": slug}).fetchone()
        return bool(row and row[0] > 0)
    except Exception:
        return False


def _get_untrained_tables(slug: str) -> list[str]:
    """Return base tables in the project schema that have NEVER been trained.
    A table is 'trained' once the pipeline writes its profile to
    dash_table_metadata. (dash_training_steps only holds global tail steps —
    KG/vectors — never per-table rows, so it can't be used here.)"""
    try:
        from db.session import get_sql_engine
        from sqlalchemy import text
        eng = get_sql_engine()
        with eng.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            all_tables = {r[0] for r in conn.execute(text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = :s AND table_type = 'BASE TABLE'"
            ), {"s": slug}).fetchall()}
            trained = {r[0] for r in conn.execute(text(
                "SELECT DISTINCT table_name FROM public.dash_table_metadata "
                "WHERE project_slug = :s"
            ), {"s": slug}).fetchall()}
        return sorted(all_tables - trained)
    except Exception as e:
        log.debug(f"auto_train: untrained-table query failed for {slug}: {e}")
        return []


async def _trigger_full_train(slug: str, tables: list[str]) -> bool:
    """Run the FULL 14-step training pipeline on the given tables by invoking the
    retrain handler directly with a trusted stub request (force=True so the
    fingerprint 'unchanged' skip is bypassed for never-trained tables). Runs
    inline on this daemon's asyncio task — long but non-blocking for the API."""
    try:
        from app.upload import retrain_project
        from app.auth import SUPER_ADMIN

        class _StubState:
            user = {"user_id": 0, "username": SUPER_ADMIN}

        class _StubReq:
            state = _StubState()
            query_params: dict = {}

            async def json(self):
                return {"force": True, "table_names": tables}

        log.info(f"auto_train: triggering FULL retrain for {slug} (untrained tables={tables})")
        await retrain_project(slug, _StubReq())  # type: ignore[arg-type]
        return True
    except Exception as e:
        log.warning(f"auto_train: full retrain failed for {slug}: {e}")
        return False


def _enqueue_retrain(slug: str, reason: str) -> bool:
    """Enqueue a retrain job. Returns True on success."""
    try:
        from db.session import get_sql_engine
        from dash.training.train_queue import enqueue_train_jobs, create_training_run
        from sqlalchemy import text

        # Get tables
        eng = get_sql_engine()
        with eng.connect() as conn:
            tables = [r[0] for r in conn.execute(text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = :s AND table_type = 'BASE TABLE'"
            ), {"s": slug}).fetchall()]

        if not tables:
            return False

        run_id = create_training_run(slug, len(tables))

        n = enqueue_train_jobs(slug, tables, run_id)
        log.info(f"auto_train: enqueued retrain for {slug} (reason={reason}, tables={len(tables)}, run_id={run_id}, jobs={n})")
        return True
    except Exception as e:
        log.warning(f"auto_train: enqueue failed for {slug}: {e}")
        return False


async def _check_and_train(slug: str) -> dict:
    """Check for changes and enqueue if needed. Returns status dict."""
    result = {"slug": slug, "action": "none", "reason": ""}

    if await _is_training_running(slug):
        result["action"] = "skipped"
        result["reason"] = "training_already_running"
        return result

    current = await _get_current_row_counts(slug)
    if not current:
        result["reason"] = "no_tables"
        return result

    # Train-untrained: any base table that has never been trained gets the FULL
    # pipeline now, regardless of row-delta. Fixes the "pre-loaded tables stay
    # untrained forever because they never change" gap.
    untrained = _get_untrained_tables(slug)
    if untrained:
        # Cooldown guard: skip if a run started recently (upload's promote+train is
        # already training these — its metadata write lags, so without this the
        # untrained-check double-fires a duplicate run that races the first).
        secs = await _seconds_since_last_run(slug)
        if secs is not None and secs < _RECENT_RUN_COOLDOWN_S:
            _last_row_counts[slug] = current
            result["action"] = "skipped"
            result["reason"] = f"recent_run_{int(secs)}s"
            return result
        ok = await _trigger_full_train(slug, untrained)
        # baseline counts so the delta path takes over next cycle
        _last_row_counts[slug] = current
        result["action"] = "trained_untrained" if ok else "train_untrained_failed"
        result["reason"] = f"untrained:{','.join(untrained)}"
        return result

    prev = _last_row_counts.get(slug, {})
    total_current = sum(current.values())
    total_prev = sum(prev.values()) if prev else 0

    # Check for new tables
    new_tables = set(current) - set(prev)
    # Check row delta
    delta_pct = 0.0
    if total_prev > 0:
        delta_pct = abs(total_current - total_prev) / total_prev * 100

    should_train = False
    reason = ""

    if not prev:
        # First check after startup — just record, don't train
        _last_row_counts[slug] = current
        result["action"] = "initialized"
        result["reason"] = "first_check"
        return result

    if new_tables:
        should_train = True
        reason = f"new_tables:{','.join(new_tables)}"
    elif delta_pct >= _ROW_DELTA_THRESHOLD:
        should_train = True
        reason = f"row_delta:{delta_pct:.1f}pct"

    if should_train:
        ok = _enqueue_retrain(slug, reason)
        if ok:
            _last_row_counts[slug] = current
            result["action"] = "enqueued"
            result["reason"] = reason
        else:
            result["action"] = "enqueue_failed"
            result["reason"] = reason
    else:
        _last_row_counts[slug] = current
        result["action"] = "no_change"
        result["reason"] = f"delta={delta_pct:.1f}pct"

    return result


_last_check_result: dict = {}
_last_check_time: float = 0.0


async def auto_train_loop():
    """Main daemon loop."""
    if _DISABLED:
        log.info("auto_train_daemon: disabled (AUTO_TRAIN_DAEMON_DISABLED=1)")
        return

    log.info(f"auto_train_daemon: started (poll={_POLL_INTERVAL}s, delta_threshold={_ROW_DELTA_THRESHOLD}%)")
    global _last_check_result, _last_check_time

    # Quick first check after 60s to baseline row counts, then full interval
    _first = True
    while True:
        await asyncio.sleep(60 if _first else _POLL_INTERVAL)
        _first = False
        try:
            slug = await _get_locked_slug()
            if not slug:
                continue
            result = await _check_and_train(slug)
            _last_check_result = result
            _last_check_time = time.time()
            if result["action"] not in ("no_change", "initialized", "skipped"):
                log.info(f"auto_train_daemon: {result}")
        except Exception as e:
            log.exception(f"auto_train_daemon: unhandled error: {e}")


def get_daemon_status() -> dict:
    """Return current daemon status for the UI endpoint."""
    return {
        "enabled": not _DISABLED,
        "poll_interval_s": _POLL_INTERVAL,
        "delta_threshold_pct": _ROW_DELTA_THRESHOLD,
        "last_check_time": _last_check_time,
        "last_check_result": _last_check_result,
        "last_row_counts": {s: sum(t.values()) for s, t in _last_row_counts.items()},
    }
