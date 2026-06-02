"""Nightly cron: re-embed docs / KG triples whose stored vector is stale.

Runs every 6h via the FastAPI lifespan background task (not k8s — that's
a separate refactor). Pulls docs whose `created_at` is newer than the matching
``dash_vectors.updated_at``, plus any docs missing a vector entirely.

Schema notes (actual ``public.dash_documents`` columns observed):
    id (serial), project_slug, filename, content, file_type, file_size, created_at

There is no ``updated_at`` / ``title`` / ``body`` column, so we COALESCE on
``filename`` for the title prefix and use ``created_at`` as the freshness
yardstick.
"""
from __future__ import annotations

import asyncio
import logging

log = logging.getLogger(__name__)

# Observability tracing (fail-soft; no-op if unavailable or TRACING_DISABLED).
try:
    from dash.obs.trace import trace_span
except Exception:  # noqa: BLE001
    from contextlib import contextmanager as _cm

    @_cm
    def trace_span(*_a, **_k):  # type: ignore
        yield None


async def reembed_stale_docs(slug: str | None = None, limit: int = 5000) -> int:
    """For each project doc newer than its vector, enqueue re-embed.

    Returns the number of rows queued.
    """
    from dash.tools.vector_sync import VECTOR_SYNC
    from db.session import get_sql_engine
    from sqlalchemy import text as sql

    queued = 0
    eng = get_sql_engine()

    # 1) Documents — compare doc.created_at against vector.updated_at.
    try:
        with eng.connect() as c:
            rows = c.execute(
                sql(
                    """
                    SELECT d.project_slug,
                           'docs'                                  AS ns,
                           d.id::text                              AS sid,
                           COALESCE(d.filename, '') || E'\\n\\n'
                             || COALESCE(d.content, '')            AS txt
                    FROM public.dash_documents d
                    LEFT JOIN dash_vectors v
                      ON v.project_slug = d.project_slug
                     AND v.namespace    = 'docs'
                     AND v.source_id    = d.id::text
                    WHERE (v.id IS NULL
                           OR (d.created_at IS NOT NULL
                               AND d.created_at > COALESCE(v.updated_at, 'epoch'::timestamptz)))
                      AND (CAST(:slug AS TEXT) IS NULL OR d.project_slug = :slug)
                    LIMIT :lim
                    """
                ),
                {"slug": slug, "lim": limit},
            ).mappings().all()
        for r in rows:
            if not r["txt"] or not r["txt"].strip():
                continue
            ok = await VECTOR_SYNC.enqueue(
                r["project_slug"], r["ns"], r["sid"], r["txt"], {}
            )
            if ok:
                queued += 1
    except Exception:
        log.exception("reembed_stale_docs: docs scan failed")

    # 2) Knowledge graph triples — re-embed by id, namespace='kg'.
    try:
        with eng.connect() as c:
            rows = c.execute(
                sql(
                    """
                    SELECT t.project_slug,
                           'kg'                                  AS ns,
                           t.id::text                            AS sid,
                           t.subject || ' ' || t.predicate || ' ' || t.object AS txt
                    FROM public.dash_knowledge_triples t
                    LEFT JOIN dash_vectors v
                      ON v.project_slug = t.project_slug
                     AND v.namespace    = 'kg'
                     AND v.source_id    = t.id::text
                    WHERE (v.id IS NULL
                           OR (t.created_at IS NOT NULL
                               AND t.created_at > COALESCE(v.updated_at, 'epoch'::timestamptz)))
                      AND (CAST(:slug AS TEXT) IS NULL OR t.project_slug = :slug)
                    LIMIT :lim
                    """
                ),
                {"slug": slug, "lim": limit},
            ).mappings().all()
        for r in rows:
            if not r["txt"] or not r["txt"].strip():
                continue
            ok = await VECTOR_SYNC.enqueue(
                r["project_slug"], r["ns"], r["sid"], r["txt"], {}
            )
            if ok:
                queued += 1
    except Exception:
        log.exception("reembed_stale_docs: kg scan failed")

    return queued


async def reembed_loop(
    interval_seconds: int = 21600,
    initial_delay_seconds: int = 1800,
) -> None:
    """Forever-loop: scan stale docs, sleep, repeat. Cancellation-safe.

    Performs an INITIAL scan after ``initial_delay_seconds`` (default 30 min
    after API start) so newly-created projects don't wait 6h for their first
    vectors. Set ``initial_delay_seconds=0`` to scan immediately (useful in
    tests or after a long cold start).

    Subsequent scans run on the ``interval_seconds`` cadence (default 6h).
    """
    # Initial fast scan — catches projects/docs/KG inserted between boot and
    # the first full-cadence tick. Falls through to the normal loop after.
    try:
        if initial_delay_seconds > 0:
            await asyncio.sleep(initial_delay_seconds)
        n = await reembed_stale_docs()
        if n:
            log.info("reembed_loop: initial scan queued %d stale rows", n)
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("reembed loop initial scan error")

    while True:
        try:
            await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            raise
        try:
            with trace_span("cron.reembed_stale", kind="cron"):
                n = await reembed_stale_docs()
            if n:
                log.info("reembed_loop: queued %d stale rows", n)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("reembed loop error")


async def enqueue_project_for_reembed(project_slug: str, limit: int = 5000) -> int:
    """Public helper: scan a single project's docs + KG triples for stale
    vectors and enqueue them for re-embedding.

    Other modules (e.g. post-train hooks, admin endpoints) can call this to
    force a project-scoped scan without waiting for the 6h cron. Returns the
    number of rows queued.
    """
    return await reembed_stale_docs(slug=project_slug, limit=limit)


__all__ = [
    "reembed_stale_docs",
    "reembed_loop",
    "enqueue_project_for_reembed",
]
