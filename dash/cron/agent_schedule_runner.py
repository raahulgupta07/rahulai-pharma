"""Dash-OS Phase 2E — Agent schedule executor daemon.

Polls dash.dash_agent_schedules every 30s. Atomic claim via UPDATE.
Executes prompt by POSTing to internal chat endpoint. 5min timeout per run.

Disable via: AGENT_SCHEDULE_RUNNER_DISABLED=1, K8S_DAEMON_MODE=cronjob,
or DAEMONS_DISABLED=1.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Observability tracing (fail-soft; no-op if unavailable or TRACING_DISABLED).
try:
    from dash.obs.trace import trace_span
except Exception:  # noqa: BLE001
    from contextlib import contextmanager as _cm

    @_cm
    def trace_span(*_a, **_k):  # type: ignore
        yield None

POLL_INTERVAL_S = int(os.getenv("AGENT_SCHEDULE_POLL_S", "30"))
RUN_TIMEOUT_S = int(os.getenv("AGENT_SCHEDULE_TIMEOUT_S", "300"))
INTERNAL_BASE = os.getenv("DASH_INTERNAL_URL", "http://127.0.0.1:8000")


def _disabled() -> bool:
    if os.getenv("AGENT_SCHEDULE_RUNNER_DISABLED") == "1":
        return True
    if os.getenv("K8S_DAEMON_MODE") == "cronjob":
        return True
    if os.getenv("DAEMONS_DISABLED") == "1":
        return True
    return False


def _get_engine():
    try:
        from db.session import get_sql_engine
        return get_sql_engine()
    except Exception:
        try:
            from db import get_sql_engine  # type: ignore
            return get_sql_engine()
        except Exception:
            return None


def _compute_next(kind: str, cron_expr: Optional[str], interval_seconds: Optional[int]) -> Optional[dt.datetime]:
    base = dt.datetime.utcnow()
    if kind == "cron" and cron_expr:
        try:
            from croniter import croniter
            return croniter(cron_expr, base).get_next(dt.datetime)
        except Exception:
            return base + dt.timedelta(hours=1)
    if kind == "interval" and interval_seconds:
        return base + dt.timedelta(seconds=interval_seconds)
    return None  # once = no rescheduling


async def _claim_due() -> list[Dict[str, Any]]:
    eng = _get_engine()
    if eng is None:
        return []
    try:
        from sqlalchemy import text
        with eng.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT id, project_slug, schedule_kind, cron_expr, interval_seconds,
                           prompt, agent_target, max_runs, run_count
                    FROM dash.dash_agent_schedules
                    WHERE enabled = true AND next_run_at <= now()
                    LIMIT 20
                    """
                )
            ).mappings().all()
        claimed = []
        for r in rows:
            with eng.begin() as conn:
                upd = conn.execute(
                    text(
                        """
                        UPDATE dash.dash_agent_schedules
                           SET last_run_at = now()
                         WHERE id = :id
                           AND enabled = true
                           AND next_run_at <= now()
                        """
                    ),
                    {"id": r["id"]},
                )
                if upd.rowcount == 1:
                    claimed.append(dict(r))
        return claimed
    except Exception as e:
        logger.warning("claim_due failed: %s", e)
        return []


async def _execute(sched: Dict[str, Any]) -> Dict[str, Any]:
    """Best-effort execution. Logs run row + updates schedule."""
    eng = _get_engine()
    if eng is None:
        return {"status": "error", "error": "db unavailable"}

    from sqlalchemy import text
    run_row_id = None
    try:
        with eng.begin() as conn:
            r = conn.execute(
                text(
                    "INSERT INTO dash.dash_agent_schedule_runs (schedule_id, status) "
                    "VALUES (:sid, 'running') RETURNING id"
                ),
                {"sid": sched["id"]},
            )
            run_row_id = r.scalar()
    except Exception as e:
        logger.warning("schedule run row insert failed: %s", e)

    # Execute prompt via internal HTTP call (best-effort)
    status = "ok"
    response_excerpt = None
    error = None
    try:
        import httpx
        slug = sched.get("project_slug") or ""
        url = f"{INTERNAL_BASE}/api/projects/{slug}/chat" if slug else f"{INTERNAL_BASE}/api/chat"
        token = os.getenv("INTERNAL_SCHEDULE_TOKEN") or os.getenv("DASH_SYSTEM_TOKEN")
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        async with httpx.AsyncClient(timeout=RUN_TIMEOUT_S) as client:
            resp = await client.post(url, json={"message": sched["prompt"]}, headers=headers)
            if resp.status_code >= 400:
                status = "error"
                error = f"http {resp.status_code}"
            else:
                response_excerpt = (resp.text or "")[:500]
    except asyncio.TimeoutError:
        status = "timeout"
        error = "timeout"
    except Exception as e:
        status = "skipped"
        error = f"chat_endpoint_unreachable: {e}"

    # Update run row + schedule
    try:
        with eng.begin() as conn:
            if run_row_id is not None:
                conn.execute(
                    text(
                        "UPDATE dash.dash_agent_schedule_runs "
                        "SET finished_at=now(), status=:st, response_excerpt=:re, error=:err "
                        "WHERE id=:id"
                    ),
                    {"st": status, "re": response_excerpt, "err": error, "id": run_row_id},
                )
            next_at = _compute_next(
                sched["schedule_kind"], sched.get("cron_expr"), sched.get("interval_seconds"),
            )
            new_count = (sched.get("run_count") or 0) + 1
            cap = sched.get("max_runs")
            new_enabled = True
            if cap and new_count >= cap:
                new_enabled = False
            if next_at is None and sched["schedule_kind"] != "once":
                new_enabled = False
            if sched["schedule_kind"] == "once":
                new_enabled = False
            conn.execute(
                text(
                    """
                    UPDATE dash.dash_agent_schedules
                       SET next_run_at = :nr,
                           run_count = :rc,
                           last_run_result = :lr,
                           last_run_error = :le,
                           enabled = :en,
                           updated_at = now()
                     WHERE id = :id
                    """
                ),
                {
                    "nr": next_at, "rc": new_count, "lr": status,
                    "le": error, "en": new_enabled, "id": sched["id"],
                },
            )
    except Exception as e:
        logger.warning("schedule run finalize failed: %s", e)
    return {"status": status, "error": error}


async def agent_schedule_loop() -> None:
    if _disabled():
        logger.info("agent_schedule_runner disabled")
        return
    logger.info("agent_schedule_runner starting (poll=%ds)", POLL_INTERVAL_S)
    while True:
        try:
            claimed = await _claim_due()
            if claimed:
                # Emit a cron span whenever this tick fires schedules.
                with trace_span("cron.agent_schedule_runner", kind="cron"):
                    for s in claimed:
                        # fire-and-forget per schedule (bounded by limit=20 in claim)
                        asyncio.create_task(_execute(s))
        except Exception as e:
            logger.warning("agent_schedule_loop iteration failed: %s", e)
        await asyncio.sleep(POLL_INTERVAL_S)
