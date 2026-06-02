"""
dash.ingest.cleanup
===================
Cleanup helper and daemon for the staged data-ingest pipeline.

Purges batches whose status is ``promoted`` or ``rejected`` and whose
``updated_at`` timestamp is older than a configurable retention window.
``staged`` (un-promoted) batches are never touched regardless of age.

Public surface
--------------
purge_old_batches(days: int = 14) -> dict
    One-shot synchronous purge.  Safe to call from a script, a cron-job
    endpoint, or any async context (run in a thread).

async cleanup_loop() -> None
    Long-running async daemon intended to be ``asyncio.create_task``-ed
    from the application lifespan.  Respects the env vars below.

Environment variables
---------------------
INGEST_CLEANUP_DISABLED
    Set to ``"1"`` (or any truthy value) to skip all cleanup work.
    ``cleanup_loop`` logs once and returns immediately.

INGEST_CLEANUP_INTERVAL_SECONDS
    Seconds between purge runs (default: ``86400`` — 24 h).

INGEST_CLEANUP_RETENTION_DAYS
    Batches older than this many days are eligible for purge
    (default: ``14``).

Rules (inherited from staging.py)
----------------------------------
- Use ``get_write_engine`` from ``db.session``; NEVER call ``.dispose()`` on it.
- JSONB parameters use ``CAST(:x AS jsonb)`` — never ``:x::jsonb``.
- All DB writes are fail-soft (try/except + log); never raise from the loop.
- Lazy-import staging helpers inside functions to avoid import cycles.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from os import getenv

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _is_truthy(value: str | None) -> bool:
    """Return True when *value* is a non-empty, non-zero string."""
    if not value:
        return False
    return value.strip().lower() not in ("0", "false", "no", "off", "")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def purge_old_batches(days: int = 14) -> dict:
    """Delete promoted/rejected batches older than *days* days.

    For each eligible batch the function:

    1. Removes the on-disk staging directory via ``shutil.rmtree``
       (``ignore_errors=True`` — a missing directory is not an error).
    2. Deletes the ``public.dash_ingest_files`` rows for that batch.
    3. Deletes the ``public.dash_ingest_batches`` row.

    Batches with ``status = 'staged'`` are **never** touched, regardless of
    age, so operators can always review un-promoted batches at their own pace.

    Parameters
    ----------
    days:
        Retention window in days.  Batches whose ``updated_at`` is strictly
        older than ``now() - <days> days`` are candidates for deletion.

    Returns
    -------
    dict
        ``{"deleted_batches": int, "deleted_dirs": int, "errors": int,
           "cutoff_days": int}``
    """
    result = {
        "deleted_batches": 0,
        "deleted_dirs": 0,
        "errors": 0,
        "cutoff_days": days,
    }

    # Lazy imports to avoid circular-import issues at module load time.
    try:
        from db.session import get_write_engine
        from sqlalchemy import text
    except Exception as exc:  # pragma: no cover
        log.error("purge_old_batches: cannot import db.session: %s", exc)
        result["errors"] += 1
        return result

    try:
        from dash.ingest.staging import batch_dir  # noqa: PLC0415
    except Exception as exc:
        log.error("purge_old_batches: cannot import dash.ingest.staging: %s", exc)
        result["errors"] += 1
        return result

    engine = get_write_engine()

    # ------------------------------------------------------------------
    # Step 1 — Discover eligible batches.
    # ------------------------------------------------------------------
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT batch_id, project_slug
                    FROM public.dash_ingest_batches
                    WHERE status IN ('promoted', 'rejected')
                      AND updated_at < now() - (:days || ' days')::interval
                    """
                ),
                {"days": str(days)},
            ).fetchall()
    except Exception as exc:
        log.error("purge_old_batches: query failed: %s", exc)
        result["errors"] += 1
        return result

    if not rows:
        log.info(
            "purge_old_batches: no eligible batches found (cutoff=%d days)", days
        )
        return result

    log.info(
        "purge_old_batches: %d batch(es) eligible for purge (cutoff=%d days)",
        len(rows),
        days,
    )

    # ------------------------------------------------------------------
    # Step 2 — Purge each batch (fail-soft per batch).
    # ------------------------------------------------------------------
    for row in rows:
        batch_id: str = row[0]
        project_slug: str = row[1]

        # 2a. Remove on-disk directory.
        try:
            d = batch_dir(project_slug, batch_id)
            shutil.rmtree(d, ignore_errors=True)
            result["deleted_dirs"] += 1
            log.debug(
                "purge_old_batches: removed dir %s (batch=%s, project=%s)",
                d,
                batch_id,
                project_slug,
            )
        except Exception as exc:
            log.warning(
                "purge_old_batches: rmtree failed for batch %s / project %s: %s",
                batch_id,
                project_slug,
                exc,
            )
            result["errors"] += 1

        # 2b. Delete dash_ingest_files rows, then the batch row itself.
        try:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "DELETE FROM public.dash_ingest_files WHERE batch_id = :bid"
                    ),
                    {"bid": batch_id},
                )
                conn.execute(
                    text(
                        "DELETE FROM public.dash_ingest_batches WHERE batch_id = :bid"
                    ),
                    {"bid": batch_id},
                )
            result["deleted_batches"] += 1
            log.debug(
                "purge_old_batches: deleted DB rows for batch %s (project=%s)",
                batch_id,
                project_slug,
            )
        except Exception as exc:
            log.warning(
                "purge_old_batches: DB delete failed for batch %s / project %s: %s",
                batch_id,
                project_slug,
                exc,
            )
            result["errors"] += 1

    log.info(
        "purge_old_batches: done — deleted_batches=%d deleted_dirs=%d errors=%d",
        result["deleted_batches"],
        result["deleted_dirs"],
        result["errors"],
    )
    return result


async def cleanup_loop() -> None:
    """Long-running async daemon that periodically purges old ingest batches.

    Intended to be launched via ``asyncio.create_task(cleanup_loop())`` from
    the application lifespan (e.g. ``app/main.py``).  Do NOT wire it here —
    the integrator handles that.

    Behaviour
    ---------
    * If ``INGEST_CLEANUP_DISABLED`` is truthy the coroutine logs once and
      returns immediately (zero overhead in production when disabled).
    * On first iteration the daemon sleeps a short *warmup* period
      (``INGEST_CLEANUP_WARMUP_SECONDS``, default ``300`` s / 5 min) so that
      application startup is not hammered.
    * Subsequent iterations sleep ``INGEST_CLEANUP_INTERVAL_SECONDS``
      (default ``86400`` s / 24 h) between runs.
    * Each purge call is wrapped in ``try/except`` so a transient DB error
      never kills the daemon.
    """
    disabled = _is_truthy(getenv("INGEST_CLEANUP_DISABLED"))
    if disabled:
        log.info(
            "cleanup_loop: INGEST_CLEANUP_DISABLED is set — cleanup daemon will not run"
        )
        return

    interval = int(getenv("INGEST_CLEANUP_INTERVAL_SECONDS") or "86400")
    retention_days = int(getenv("INGEST_CLEANUP_RETENTION_DAYS") or "14")
    warmup = int(getenv("INGEST_CLEANUP_WARMUP_SECONDS") or "300")

    log.info(
        "cleanup_loop: starting — warmup=%ds interval=%ds retention=%dd",
        warmup,
        interval,
        retention_days,
    )

    # Warmup sleep so startup isn't hammered.
    await asyncio.sleep(warmup)

    while True:
        try:
            result = purge_old_batches(days=retention_days)
            log.info("cleanup_loop: purge complete — %s", result)
        except Exception as exc:  # noqa: BLE001
            log.error("cleanup_loop: unexpected error during purge: %s", exc)

        await asyncio.sleep(interval)
