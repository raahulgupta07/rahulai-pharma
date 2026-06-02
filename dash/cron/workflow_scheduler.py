"""Workflow cron scheduler — fires due rows in dash.dash_workflow_schedules.

Pattern mirrors dash.cron.table_usage_refresh:
  - async loop honoring DAEMONS_DISABLED / K8S_DAEMON_MODE via the
    caller's _should_run_daemons() gate (we additionally honor
    WORKFLOW_SCHEDULER_DISABLED=1).
  - configurable interval via WORKFLOW_SCHEDULER_INTERVAL_SECONDS
    (default 30s).
  - fail-soft: per-schedule try/except so one bad cron expression doesn't
    kill the batch; outer loop catches and logs to never die.

Per tick:
  1. SELECT up to 50 'active' rows where next_run_at IS NULL OR <= now()
  2. For each: atomic claim via UPDATE ... RETURNING (multi-worker safe —
     loser sees 0 rows and skips).
  3. Trigger workflow via direct call to dash.templates.runner.execute_workflow
     (reusing the SAME path as agent_os_workflows.run_now). Persist run_id
     via the existing _persist_run_start / _persist_run_finish helpers so
     dash.dash_workflow_run_history grows the same way manual runs do
     (with triggered_by='cron').
  4. Compute next_run_at via croniter; update schedule row.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Observability tracing — fail-soft no-op if unavailable.
try:
    from dash.obs.trace import trace_span
except Exception:  # noqa: BLE001
    from contextlib import contextmanager as _cm

    @_cm
    def trace_span(*_a, **_k):  # type: ignore
        yield None


DEFAULT_INTERVAL_SECONDS = 30
DEFAULT_BATCH = 50


def _is_disabled() -> bool:
    return os.getenv("WORKFLOW_SCHEDULER_DISABLED", "").lower() in (
        "1", "true", "yes",
    )


def _interval_seconds() -> int:
    raw = os.getenv("WORKFLOW_SCHEDULER_INTERVAL_SECONDS", "")
    try:
        v = int(raw)
        if v > 0:
            return v
    except Exception:
        pass
    return DEFAULT_INTERVAL_SECONDS


def _engine():
    """NullPool engine. Caller disposes."""
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool
    from db import db_url
    return create_engine(db_url, poolclass=NullPool)


def _compute_next(cron_expr: str, base: Optional[datetime] = None) -> Optional[datetime]:
    try:
        from croniter import croniter
    except Exception:
        return None
    try:
        b = base or datetime.now(timezone.utc)
        if b.tzinfo is None:
            b = b.replace(tzinfo=timezone.utc)
        return croniter(cron_expr, b).get_next(datetime)
    except Exception:
        return None


def _fire_workflow(wf_id: int, project_slug: Optional[str]) -> Optional[str]:
    """Run a workflow via the same path as agent_os_workflows.run_now.

    Returns run_id (str) or None on failure to even start.
    Persists status via existing dash_workflow_run_history infra.
    """
    try:
        from sqlalchemy import text
        from app.agent_os_workflows import (
            _persist_run_start,
            _persist_run_finish,
        )
        from dash.templates.runner import execute_workflow
    except Exception:
        logger.exception("workflow_scheduler: imports failed for wf=%s", wf_id)
        return None

    eng = _engine()
    try:
        with eng.connect() as cn:
            from sqlalchemy import text as _t
            row = cn.execute(_t(
                """
                SELECT id, project_slug, template_name, name, schedule,
                       resolved_query, action, expected_entity, expected_columns
                  FROM dash.dash_autonomous_workflows
                 WHERE id = :id
                """
            ), {"id": wf_id}).fetchone()
    except Exception:
        logger.exception("workflow_scheduler: load wf=%s failed", wf_id)
        eng.dispose()
        return None
    finally:
        try:
            eng.dispose()
        except Exception:
            pass

    if not row:
        logger.warning("workflow_scheduler: wf=%s not found", wf_id)
        return None

    wf = {
        "id": row[0], "project_slug": row[1], "template_name": row[2],
        "name": row[3], "schedule": row[4], "resolved_query": row[5],
        "action": row[6], "expected_entity": row[7],
        "expected_columns": row[8] or [],
    }

    slug = wf.get("project_slug") or project_slug or ""
    run_id = _persist_run_start(wf_id, slug, trigger="cron")

    status = "done"
    error: Optional[str] = None
    output: Any = None
    try:
        rows, err = execute_workflow(wf)
        if err:
            error = err
            status = "fail"
        else:
            output = {"rows": rows or [], "n_rows": len(rows or [])}
    except Exception as e:
        logger.exception("workflow_scheduler: execute wf=%s failed", wf_id)
        error = str(e)[:500]
        status = "fail"

    try:
        _persist_run_finish(run_id, status=status, output=output, error=error)
    except Exception:
        logger.exception("workflow_scheduler: persist_finish run=%s failed", run_id)

    return run_id


def run_cycle() -> dict:
    """One tick. Sync; called via asyncio.to_thread."""
    fired = 0
    skipped = 0
    errors = 0
    eng = _engine()
    try:
        from sqlalchemy import text
        with eng.connect() as cn:
            due = cn.execute(text(
                """
                SELECT id, workflow_id, cron, project_slug, owner_user_id
                  FROM dash.dash_workflow_schedules
                 WHERE status = 'active'
                   AND (next_run_at IS NULL OR next_run_at <= now())
                 ORDER BY next_run_at NULLS FIRST
                 LIMIT :lim
                """
            ), {"lim": DEFAULT_BATCH}).fetchall()
    except Exception as e:
        logger.exception("workflow_scheduler: select due failed")
        try:
            eng.dispose()
        except Exception:
            pass
        return {"fired": 0, "skipped": 0, "errors": 1, "error": str(e)}

    for r in due:
        sched_id = r[0]
        wf_id = r[1]
        cron_expr = r[2]
        slug = r[3]
        # owner_user_id = r[4]  # reserved for future auth scoping
        try:
            # atomic claim
            from sqlalchemy import text
            with eng.begin() as cn:
                claimed = cn.execute(text(
                    """
                    UPDATE dash.dash_workflow_schedules
                       SET last_run_at = now()
                     WHERE id = :id
                       AND status = 'active'
                       AND (next_run_at IS NULL OR next_run_at <= now())
                     RETURNING workflow_id, owner_user_id
                    """
                ), {"id": sched_id}).fetchone()
            if not claimed:
                skipped += 1
                continue

            run_id_str = _fire_workflow(wf_id, slug)

            # compute next + persist
            nxt = _compute_next(cron_expr)
            try:
                from sqlalchemy import text
                with eng.begin() as cn:
                    cn.execute(text(
                        """
                        UPDATE dash.dash_workflow_schedules
                           SET next_run_at = :nxt
                         WHERE id = :id
                        """
                    ), {"nxt": nxt, "id": sched_id})
            except Exception:
                logger.exception(
                    "workflow_scheduler: update next_run_at failed sched=%s", sched_id
                )

            fired += 1
        except Exception:
            errors += 1
            logger.exception(
                "workflow_scheduler: tick failed sched=%s wf=%s", sched_id, wf_id
            )

    try:
        eng.dispose()
    except Exception:
        pass

    return {"fired": fired, "skipped": skipped, "errors": errors,
            "candidates": len(due)}


async def workflow_scheduler_loop() -> None:
    if _is_disabled():
        logger.info("workflow_scheduler: disabled via env")
        return
    interval = _interval_seconds()
    logger.info("workflow_scheduler: starting (interval=%ds)", interval)
    while True:
        try:
            with trace_span("cron.workflow_scheduler", kind="cron"):
                stats = await asyncio.to_thread(run_cycle)
            logger.info(
                "workflow_scheduler: cycle_done fired=%d skipped=%d errors=%d",
                stats.get("fired", 0),
                stats.get("skipped", 0),
                stats.get("errors", 0),
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("workflow_scheduler: outer loop crashed")
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise


__all__ = ["run_cycle", "workflow_scheduler_loop"]
