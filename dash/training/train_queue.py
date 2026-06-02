"""
Redis-backed training job queue + per-project locks + in-process worker loop.
==============================================================================

Option B chassis: piggybacks on existing dash-api workers + Redis (already
running per docker ps). No separate worker container, no resurrection of the
deleted ml_worker infra.

Public API:
    enqueue_train_jobs(slug, tables, run_id, steps=None) -> int
    claim_next_job(worker_id: str) -> dict | None
    complete_job(job_id, result, ok=True, error=None) -> None
    get_run_status(run_id) -> dict
    create_training_run(slug, n_tables) -> int
    cancel_run(run_id) -> int
    run_worker_loop() -> None        # blocking, called from app lifespan
    _dispatch_job(job) -> tuple[bool, dict|None, str|None]

Tables: public.dash_training_jobs (migration 170), public.dash_training_runs
(existing).

Redis: dash:training:queue (LIST, LPUSH new / RPOP claim).
       dash:training:project_lock:{slug} (5min TTL string lock).

Kill switch: TRAINING_QUEUE_DISABLED=1 → worker loop exits, enqueue still
inserts rows but returns count so callers can detect.

PgBouncer rules: CAST(:p AS jsonb) not :p::jsonb. get_write_engine() for
public.dash_* writes.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import socket
import threading
import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

logger = logging.getLogger(__name__)

_REDIS_KEY = "dash:training:queue"
_LOCK_KEY_FMT = "dash:training:project_lock:{slug}"
_LOCK_TTL_S = 300  # 5 min — covers a long single-table profile run
_JOB_TIMEOUT_S = 300
_DEFAULT_STEPS = ["profile_v2"]


# --------------------------------------------------------------------------
# Redis client (fail-soft)
# --------------------------------------------------------------------------
_redis_client = None


def _get_redis():
    """Return module-level redis client or None on failure."""
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    try:
        import redis  # type: ignore
        url = os.getenv("REDIS_URL", "redis://dash-redis:6379")
        _redis_client = redis.from_url(url, socket_timeout=2, socket_connect_timeout=2)
        # ping to confirm
        _redis_client.ping()
        return _redis_client
    except Exception as e:
        logger.warning(f"train_queue: Redis unavailable ({e}); claims will return None")
        _redis_client = None
        return None


def _disabled() -> bool:
    return os.getenv("TRAINING_QUEUE_DISABLED", "").lower() in ("1", "true", "yes")


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------
def create_training_run(slug: str, n_tables: int) -> int:
    """Insert public.dash_training_runs row (status='running'). Returns id.

    Fail-soft on missing columns — minimal insert.
    """
    from db.session import get_write_engine
    eng = get_write_engine()
    with eng.begin() as conn:
        row = conn.execute(text(
            "INSERT INTO public.dash_training_runs "
            "(project_slug, status, started_at) "
            "VALUES (:s, 'running', now()) RETURNING id"
        ), {"s": slug}).fetchone()
        return int(row[0])


def enqueue_train_jobs(
    slug: str,
    tables: list[str],
    run_id: int,
    steps: list[str] | None = None,
) -> int:
    """Insert N rows into dash_training_jobs (status='queued') + LPUSH ids
    onto Redis list. Returns count enqueued.

    Default steps = ['profile_v2']. Each table = 1 job carrying the steps list.
    """
    if not tables:
        return 0
    steps = steps or list(_DEFAULT_STEPS)
    from db.session import get_write_engine
    eng = get_write_engine()
    payload = {"steps": steps}
    payload_json = json.dumps(payload)

    job_ids: list[int] = []
    with eng.begin() as conn:
        for tbl in tables:
            row = conn.execute(text(
                "INSERT INTO public.dash_training_jobs "
                "(run_id, project_slug, table_name, job_type, status, payload) "
                "VALUES (:rid, :slug, :tbl, :jt, 'queued', CAST(:p AS jsonb)) "
                "RETURNING id"
            ), {
                "rid": run_id, "slug": slug, "tbl": tbl,
                "jt": "table_train", "p": payload_json,
            }).fetchone()
            if row:
                job_ids.append(int(row[0]))

    # Push to Redis (best effort)
    r = _get_redis()
    if r is not None and job_ids:
        try:
            r.lpush(_REDIS_KEY, *[str(j) for j in job_ids])
        except Exception as e:
            logger.warning(f"train_queue: redis lpush failed ({e}); jobs still in DB")

    return len(job_ids)


def _fetch_job(job_id: int) -> dict | None:
    from db.session import get_write_engine
    eng = get_write_engine()
    with eng.begin() as conn:
        row = conn.execute(text(
            "SELECT id, run_id, project_slug, table_name, job_type, status, payload "
            "FROM public.dash_training_jobs WHERE id = :id"
        ), {"id": job_id}).fetchone()
        if not row:
            return None
        payload = row[6]
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                payload = {}
        return {
            "id": int(row[0]),
            "run_id": int(row[1]) if row[1] is not None else None,
            "project_slug": row[2],
            "table_name": row[3],
            "job_type": row[4],
            "status": row[5],
            "payload": payload or {},
        }


def claim_next_job(worker_id: str) -> dict | None:
    """RPOP from Redis. Atomic UPDATE status='queued'→'running'.
    Per-project lock check: if another job for same slug is running, re-queue
    the claim (LPUSH back) and return None to skip this tick.
    Returns full job dict or None.
    """
    r = _get_redis()
    if r is None:
        return None
    try:
        raw = r.rpop(_REDIS_KEY)
    except Exception as e:
        logger.warning(f"train_queue: redis rpop failed ({e})")
        return None
    if raw is None:
        return None
    try:
        job_id = int(raw)
    except Exception:
        return None

    job = _fetch_job(job_id)
    if not job:
        return None

    # Per-project lock check
    slug = job.get("project_slug") or ""
    lock_key = _LOCK_KEY_FMT.format(slug=slug)
    try:
        # SET NX EX — atomic acquire
        got = r.set(lock_key, worker_id, nx=True, ex=_LOCK_TTL_S)
        if not got:
            # Someone else owns the project — re-queue and move on
            try:
                r.lpush(_REDIS_KEY, str(job_id))
            except Exception:
                pass
            return None
    except Exception as e:
        logger.warning(f"train_queue: lock check failed ({e}); proceeding without lock")

    # Atomic claim: only mark running if still queued
    from db.session import get_write_engine
    eng = get_write_engine()
    with eng.begin() as conn:
        res = conn.execute(text(
            "UPDATE public.dash_training_jobs "
            "SET status='running', started_at=now() "
            "WHERE id = :id AND status='queued'"
        ), {"id": job_id})
        if (res.rowcount or 0) == 0:
            # Lost the race; release lock
            try:
                if r is not None:
                    r.delete(lock_key)
            except Exception:
                pass
            return None

    job["status"] = "running"
    return job


def complete_job(
    job_id: int,
    result: dict | None,
    ok: bool = True,
    error: str | None = None,
) -> None:
    """UPDATE row: status='done'|'failed', result jsonb, finished_at.
    Also releases per-project lock.
    """
    from db.session import get_write_engine
    eng = get_write_engine()
    status = "done" if ok else "failed"
    result_json = json.dumps(result or {})
    slug = None
    with eng.begin() as conn:
        row = conn.execute(text(
            "UPDATE public.dash_training_jobs "
            "SET status = :st, result = CAST(:r AS jsonb), error = :err, finished_at = now() "
            "WHERE id = :id RETURNING project_slug"
        ), {
            "st": status, "r": result_json, "err": error, "id": job_id,
        }).fetchone()
        if row:
            slug = row[0]

    # Release project lock
    r = _get_redis()
    if r is not None and slug:
        try:
            r.delete(_LOCK_KEY_FMT.format(slug=slug))
        except Exception:
            pass

    # Finalize run: if all jobs for this run done/failed, flip run status
    try:
        with eng.begin() as conn:
            row = conn.execute(text(
                "SELECT run_id FROM public.dash_training_jobs WHERE id = :id"
            ), {"id": job_id}).fetchone()
            if not row or row[0] is None:
                return
            run_id = int(row[0])
            stats = conn.execute(text(
                "SELECT "
                "COUNT(*) FILTER (WHERE status IN ('queued','running')) AS pending, "
                "COUNT(*) FILTER (WHERE status = 'failed') AS failed, "
                "COUNT(*) AS total "
                "FROM public.dash_training_jobs WHERE run_id = :rid"
            ), {"rid": run_id}).fetchone()
            if stats and int(stats[0]) == 0 and int(stats[2]) > 0:
                final = "failed" if int(stats[1]) > 0 else "done"
                conn.execute(text(
                    "UPDATE public.dash_training_runs "
                    "SET status = :st, finished_at = COALESCE(finished_at, now()) "
                    "WHERE id = :rid AND status NOT IN ('done','failed','cancelled')"
                ), {"st": final, "rid": run_id})
    except Exception:
        pass


def get_run_status(run_id: int) -> dict:
    """Aggregate dash_training_jobs by run_id. Returns rollup dict."""
    from db.session import get_write_engine
    eng = get_write_engine()
    with eng.begin() as conn:
        rows = conn.execute(text(
            "SELECT status, COUNT(*) AS n, MIN(started_at) AS first_started, "
            "MAX(finished_at) AS last_finished "
            "FROM public.dash_training_jobs WHERE run_id = :rid GROUP BY status"
        ), {"rid": run_id}).fetchall()
        errors_rows = conn.execute(text(
            "SELECT table_name, error FROM public.dash_training_jobs "
            "WHERE run_id = :rid AND status = 'failed' AND error IS NOT NULL "
            "ORDER BY finished_at DESC NULLS LAST LIMIT 20"
        ), {"rid": run_id}).fetchall()

    counts: dict[str, int] = {"queued": 0, "running": 0, "done": 0, "failed": 0, "cancelled": 0}
    started_at = None
    finished_at = None
    for st, n, fs, lf in rows:
        counts[str(st)] = int(n)
        if fs and (started_at is None or fs < started_at):
            started_at = fs
        if lf and (finished_at is None or lf > finished_at):
            finished_at = lf
    total = sum(counts.values())
    completed = counts.get("done", 0)
    failed = counts.get("failed", 0)
    queued = counts.get("queued", 0)
    running = counts.get("running", 0)

    if total == 0:
        agg = "unknown"
    elif queued == 0 and running == 0 and failed == 0:
        agg = "done"
    elif queued == 0 and running == 0 and failed > 0 and completed == 0:
        agg = "failed"
    elif queued == 0 and running == 0 and failed > 0:
        agg = "partial"
    elif running > 0:
        agg = "running"
    else:
        agg = "queued"

    errors = [{"table_name": r[0], "error": r[1]} for r in errors_rows]

    return {
        "run_id": run_id,
        "status": agg,
        "total_jobs": total,
        "completed_jobs": completed,
        "failed_jobs": failed,
        "queued_jobs": queued,
        "running_jobs": running,
        "started_at": started_at.isoformat() if started_at else None,
        "finished_at": finished_at.isoformat() if finished_at else None,
        "errors": errors,
    }


def cancel_run(run_id: int) -> int:
    """Mark all queued+running jobs for run_id as cancelled. Returns count."""
    from db.session import get_write_engine
    eng = get_write_engine()
    with eng.begin() as conn:
        res = conn.execute(text(
            "UPDATE public.dash_training_jobs "
            "SET status='cancelled', finished_at=now() "
            "WHERE run_id = :rid AND status IN ('queued','running')"
        ), {"rid": run_id})
        return int(res.rowcount or 0)


# --------------------------------------------------------------------------
# Dispatch (route job_type → handler)
# --------------------------------------------------------------------------
def _dispatch_job(job: dict) -> tuple[bool, dict | None, str | None]:
    """Route by payload['steps']. Returns (ok, result_dict, error_str).

    profile_v2 → calls dash.training.profile_v2.profile_table_v2.
    """
    slug = job.get("project_slug") or ""
    table = job.get("table_name") or ""
    payload = job.get("payload") or {}
    steps = payload.get("steps") or _DEFAULT_STEPS

    results: dict[str, Any] = {}
    try:
        for step in steps:
            if step == "profile_v2":
                from dash.training.profile_v2 import profile_table_v2
                r = profile_table_v2(slug, table)
                results["profile_v2"] = r
                if isinstance(r, dict) and r.get("error"):
                    return False, results, str(r.get("error"))
            else:
                results[step] = {"skipped": True, "reason": "unknown_step"}
        return True, results, None
    except Exception as e:
        logger.exception(f"train_queue dispatch failed for job {job.get('id')}")
        return False, results or None, str(e)


# --------------------------------------------------------------------------
# Worker loop (blocking, sync — call from asyncio.to_thread in lifespan)
# --------------------------------------------------------------------------
def _install_timeout(seconds: int):
    """Install SIGALRM with given timeout. Main-thread only."""
    def _handler(signum, frame):
        raise TimeoutError(f"job exceeded {seconds}s")
    try:
        signal.signal(signal.SIGALRM, _handler)
        signal.alarm(seconds)
        return True
    except (ValueError, AttributeError):
        # Not main thread or platform without SIGALRM
        return False


def _clear_timeout():
    try:
        signal.alarm(0)
    except Exception:
        pass


def run_worker_loop() -> None:
    """Forever loop: claim job → dispatch → complete. Sleep 1s when idle.

    Disabled when TRAINING_QUEUE_DISABLED=1.
    Per-job SIGALRM timeout of _JOB_TIMEOUT_S seconds.
    """
    if _disabled():
        logger.info("train_queue: TRAINING_QUEUE_DISABLED=1, worker loop exiting")
        return

    worker_id = f"{socket.gethostname()}:{os.getpid()}:{threading.get_ident()}"
    logger.info(f"train_queue: worker loop started ({worker_id})")
    backoff_s = 1.0

    while True:
        if _disabled():
            logger.info("train_queue: disabled at runtime, exiting loop")
            return
        try:
            job = claim_next_job(worker_id)
        except Exception as e:
            logger.warning(f"train_queue: claim error ({e}); sleeping")
            time.sleep(backoff_s)
            continue

        if not job:
            time.sleep(backoff_s)
            continue

        job_id = job.get("id")
        timeout_installed = _install_timeout(_JOB_TIMEOUT_S)
        try:
            ok, result, err = _dispatch_job(job)
            if timeout_installed:
                _clear_timeout()
            complete_job(int(job_id), result, ok=ok, error=err)
        except TimeoutError:
            _clear_timeout()
            logger.warning(f"train_queue: job {job_id} timeout")
            complete_job(int(job_id), None, ok=False, error="timeout")
        except Exception as e:
            _clear_timeout()
            logger.exception(f"train_queue: unexpected job {job_id} error")
            complete_job(int(job_id), None, ok=False, error=str(e))
