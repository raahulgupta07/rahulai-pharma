"""Dash B5 — Minion worker: claim + dispatch + complete/fail.

Real implementations for the four maintenance kinds.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import traceback
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy import text

from . import queue as q

logger = logging.getLogger(__name__)

# Observability tracing (fail-soft; no-op if unavailable or TRACING_DISABLED).
try:
    from dash.obs.trace import trace_span
except Exception:  # noqa: BLE001
    from contextlib import contextmanager as _cm

    @_cm
    def trace_span(*_a, **_k):  # type: ignore
        yield None


def _run_async_safely(coro):
    """Run async coroutine whether or not we're already inside an event loop."""
    import asyncio
    try:
        asyncio.get_running_loop()
        # we ARE inside a loop — run in worker thread with its own loop
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            return ex.submit(asyncio.run, coro).result()
    except RuntimeError:
        # no running loop — safe to use asyncio.run directly
        return asyncio.run(coro)


_HANDLERS: Dict[str, Callable[[Dict[str, Any], Any], Dict[str, Any]]] = {}


def register_handler(kind: str, fn: Callable[[Dict[str, Any], Any], Dict[str, Any]]) -> None:
    _HANDLERS[kind] = fn
    # Auto-upsert into Agent OS registry (fail-soft). Keeps Fleet view alive
    # for any newly-added minion kinds without requiring a manual migration.
    try:
        _ensure_registry_row(kind)
    except Exception:
        pass


def _ensure_registry_row(kind: str) -> None:
    """Best-effort INSERT ON CONFLICT — bumps last_seen_at on every boot."""
    eng = _engine()
    with eng.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO public.dash_agent_registry
                  (agent_name, display_name, category, status,
                   handler_kind, trigger_model, last_seen_at)
                VALUES (:nm, :nm, 'auto-detected', 'active',
                        :k, 'minion_queue', now())
                ON CONFLICT (agent_name) DO UPDATE SET
                  last_seen_at = now(),
                  status = 'active',
                  handler_kind = COALESCE(public.dash_agent_registry.handler_kind, EXCLUDED.handler_kind),
                  trigger_model = COALESCE(public.dash_agent_registry.trigger_model, EXCLUDED.trigger_model)
                """
            ),
            {"nm": kind.replace("_", "-"), "k": kind},
        )


def _engine():
    from db.session import get_sql_engine
    return get_sql_engine()


# ── built-in handlers ─────────────────────────────────────────────

def handle_dedupe_entities(payload: Dict[str, Any], engine) -> Dict[str, Any]:
    project = payload.get("project")
    dry_run = bool(payload.get("dry_run", False))
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT id, kind, name_normalized
                    FROM dash.dash_entities
                    WHERE CAST(:p AS TEXT) IS NULL OR project_slug = :p
                    ORDER BY kind, name_normalized, id
                    """
                ),
                {"p": project},
            ).mappings().all()

        groups: Dict[tuple, List[int]] = {}
        for r in rows:
            key = (r["kind"], r["name_normalized"])
            groups.setdefault(key, []).append(int(r["id"]))

        dup_groups = {k: v for k, v in groups.items() if len(v) > 1}
        groups_found = len(dup_groups)
        duplicates_merged = 0
        links_remapped = 0

        if not dry_run and dup_groups:
            with engine.begin() as conn:
                for _, ids in dup_groups.items():
                    canonical = ids[0]
                    dup_ids = ids[1:]
                    # Remap src links — delete any that would conflict, then update
                    r1 = conn.execute(
                        text(
                            """
                            DELETE FROM dash.dash_entity_links a
                            USING dash.dash_entity_links b
                            WHERE a.src_entity_id = ANY(:dups)
                              AND b.src_entity_id = :canon
                              AND a.rel = b.rel
                              AND a.dst_entity_id = b.dst_entity_id
                              AND COALESCE(a.project_slug,'') = COALESCE(b.project_slug,'')
                              AND a.id <> b.id
                            """
                        ),
                        {"dups": dup_ids, "canon": canonical},
                    )
                    r2 = conn.execute(
                        text(
                            "UPDATE dash.dash_entity_links SET src_entity_id = :canon "
                            "WHERE src_entity_id = ANY(:dups)"
                        ),
                        {"canon": canonical, "dups": dup_ids},
                    )
                    # Remap dst links — same pattern
                    r3 = conn.execute(
                        text(
                            """
                            DELETE FROM dash.dash_entity_links a
                            USING dash.dash_entity_links b
                            WHERE a.dst_entity_id = ANY(:dups)
                              AND b.dst_entity_id = :canon
                              AND a.rel = b.rel
                              AND a.src_entity_id = b.src_entity_id
                              AND COALESCE(a.project_slug,'') = COALESCE(b.project_slug,'')
                              AND a.id <> b.id
                            """
                        ),
                        {"dups": dup_ids, "canon": canonical},
                    )
                    r4 = conn.execute(
                        text(
                            "UPDATE dash.dash_entity_links SET dst_entity_id = :canon "
                            "WHERE dst_entity_id = ANY(:dups)"
                        ),
                        {"canon": canonical, "dups": dup_ids},
                    )
                    links_remapped += (r2.rowcount or 0) + (r4.rowcount or 0)
                    # Delete duplicates
                    rd = conn.execute(
                        text("DELETE FROM dash.dash_entities WHERE id = ANY(:dups)"),
                        {"dups": dup_ids},
                    )
                    duplicates_merged += rd.rowcount or 0

        return {
            "ok": True,
            "project": project,
            "groups_found": groups_found,
            "duplicates_merged": duplicates_merged,
            "links_remapped": links_remapped,
            "dry_run": dry_run,
        }
    except Exception as e:
        logger.exception("dedupe_entities failed")
        return {"ok": False, "error": str(e)}


def handle_recompile_stale_pages(payload: Dict[str, Any], engine) -> Dict[str, Any]:
    project = payload.get("project")
    max_pages = int(payload.get("max_pages", 20))
    min_new = int(payload.get("min_new_evidence", 3))
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT p.id, p.compiled_at, COUNT(e.id) AS new_evidence
                    FROM dash.dash_pages p
                    LEFT JOIN dash.dash_page_evidence e
                      ON e.page_id = p.id
                     AND (p.compiled_at IS NULL OR e.ts > p.compiled_at)
                    WHERE CAST(:p AS TEXT) IS NULL OR p.project_slug = :p
                    GROUP BY p.id, p.compiled_at
                    HAVING COUNT(e.id) >= :min_new
                    ORDER BY COUNT(e.id) DESC
                    LIMIT :lim
                    """
                ),
                {"p": project, "min_new": min_new, "lim": max_pages},
            ).mappings().all()

        candidates = len(rows)
        recompiled = 0
        failures: List[int] = []
        try:
            from dash.memory.pages import recompile as _recompile
        except Exception as e:
            return {"ok": False, "error": f"recompile import failed: {e}"}

        for r in rows:
            pid = int(r["id"])
            try:
                _recompile(pid)
                recompiled += 1
            except Exception:
                logger.exception("recompile page %s failed", pid)
                failures.append(pid)

        return {
            "ok": True,
            "project": project,
            "candidates": candidates,
            "recompiled": recompiled,
            "failures": failures,
        }
    except Exception as e:
        logger.exception("recompile_stale_pages failed")
        return {"ok": False, "error": str(e)}


def handle_reembed_stale_chunks(payload: Dict[str, Any], engine) -> Dict[str, Any]:
    project = payload.get("project")
    max_chunks = int(payload.get("max_chunks", 100))
    current_model = payload.get("current_model")
    if not current_model:
        try:
            from dash import settings as _settings  # type: ignore
            current_model = getattr(_settings, "EMBEDDING_MODEL", None)
        except Exception:
            current_model = None
        if not current_model:
            current_model = "text-embedding-3-small"

    try:
        try:
            from dash.tools.embeddings_helper import embed_batch, vec_to_pg
        except Exception as e:
            return {"ok": False, "error": f"embedder unavailable: {e}"}

        # dash_vectors has no `embedding_model` column (migration 028) → treat all
        # rows for the project as candidates and limit. Caller-friendly: only
        # re-embeds up to `max_chunks` per cycle so it's bounded.
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT id, text
                    FROM dash.dash_vectors
                    WHERE project_slug = :p
                    ORDER BY updated_at ASC NULLS FIRST, id ASC
                    LIMIT :lim
                    """
                ),
                {"p": project, "lim": max_chunks},
            ).mappings().all()

        candidates = len(rows)
        reembedded = 0
        failures = 0

        BATCH = 32
        for i in range(0, len(rows), BATCH):
            batch = rows[i : i + BATCH]
            texts = [r["text"] or "" for r in batch]
            try:
                vecs = _run_async_safely(embed_batch(texts, model=current_model))
            except Exception:
                logger.exception("embed_batch failed for batch starting %d", i)
                failures += len(batch)
                continue
            try:
                with engine.begin() as conn:
                    for r, v in zip(batch, vecs):
                        conn.execute(
                            text(
                                "UPDATE dash.dash_vectors "
                                "SET embedding = CAST(:v AS vector), updated_at = NOW() "
                                "WHERE id = :id"
                            ),
                            {"v": vec_to_pg(v), "id": int(r["id"])},
                        )
                        reembedded += 1
            except Exception:
                logger.exception("persist re-embed batch failed at %d", i)
                failures += len(batch)

        return {
            "ok": True,
            "project": project,
            "candidates": candidates,
            "reembedded": reembedded,
            "model": current_model,
            "failures": failures,
        }
    except Exception as e:
        logger.exception("reembed_stale_chunks failed")
        return {"ok": False, "error": str(e)}


def handle_prune_old_evidence(payload: Dict[str, Any], engine) -> Dict[str, Any]:
    project = payload.get("project")
    max_age_days = int(payload.get("max_age_days", 180))
    keep_min = int(payload.get("keep_min_per_page", 10))
    try:
        with engine.begin() as conn:
            rows = conn.execute(
                text(
                    """
                    WITH ranked AS (
                      SELECT e.id,
                             e.page_id,
                             ROW_NUMBER() OVER (PARTITION BY e.page_id ORDER BY e.ts DESC) AS rn
                      FROM dash.dash_page_evidence e
                      JOIN dash.dash_pages p ON p.id = e.page_id
                      WHERE (CAST(:p AS TEXT) IS NULL OR p.project_slug = :p)
                        AND e.ts < NOW() - (:d || ' days')::interval
                    )
                    DELETE FROM dash.dash_page_evidence
                    WHERE id IN (SELECT id FROM ranked WHERE rn > :keep_min)
                    RETURNING id
                    """
                ),
                {"p": project, "d": str(max_age_days), "keep_min": keep_min},
            ).all()
        deleted = len(rows)
        return {
            "ok": True,
            "project": project,
            "deleted": deleted,
            "max_age_days": max_age_days,
            "keep_min": keep_min,
        }
    except Exception as e:
        logger.exception("prune_old_evidence failed")
        return {"ok": False, "error": str(e)}


register_handler("dedupe_entities", handle_dedupe_entities)
register_handler("recompile_stale_pages", handle_recompile_stale_pages)
register_handler("reembed_stale_chunks", handle_reembed_stale_chunks)
register_handler("prune_old_evidence", handle_prune_old_evidence)


def handle_brainbench_auto_capture(payload: Dict[str, Any], engine) -> Dict[str, Any]:
    """Daily BrainBench auto-capture cycle (sch_brainbench001).

    BrainBench feature removed — no-op stub kept so any existing
    `sch_brainbench001` schedule row resolves to a registered handler
    instead of erroring as "unknown handler".
    """
    return {"ok": True, "skipped": "brainbench feature removed"}


register_handler("brainbench_auto_capture", handle_brainbench_auto_capture)


# ── Dream Advanced — Tier 1 + Tier 2 handlers ───────────────────────────

def handle_poignancy_capture(payload: Dict[str, Any], engine) -> Dict[str, Any]:
    """Batch-recovery scan: insert recent chat turns missing from episode_buffer."""
    try:
        from dash.learning.dream_poignancy import batch_recover
        project = payload.get("project")
        limit = int(payload.get("limit", 200))
        stats = batch_recover(project_slug=project, limit=limit) or {}
        return {"ok": True, "project": project, **stats}
    except Exception as e:
        logger.exception("poignancy_capture failed")
        return {"ok": False, "error": str(e)}


def handle_dream_lite(payload: Dict[str, Any], engine) -> Dict[str, Any]:
    """Run one Tier 2 between-turn dream-lite cycle."""
    try:
        from dash.learning.dream_lite import run_lite_cycle
        project = payload.get("project")
        session_id = payload.get("session_id")
        if not project or not session_id:
            return {"ok": False, "error": "project + session_id required"}
        result = run_lite_cycle(
            project_slug=project,
            session_id=session_id,
            user_id=payload.get("user_id"),
            trigger_reason=payload.get("trigger_reason", "minion"),
        ) or {}
        # bootstrap_ok = hook fired before any chat episodes (not a failure).
        # done = real reflection cycle ran.
        ok_statuses = {"done", "bootstrap_ok"}
        return {"ok": result.get("status") in ok_statuses, **result}
    except Exception as e:
        logger.exception("dream_lite failed")
        return {"ok": False, "error": str(e)}


register_handler("poignancy_capture", handle_poignancy_capture)
register_handler("dream_lite", handle_dream_lite)


# ── AutoSim handlers removed — dash/autosim/ deleted ─────────────────


# W6 AutoSim comparison + marketplace handlers removed — dash/autosim/ deleted.


def run_one(worker_id: str = "minion-worker", kinds: Optional[List[str]] = None) -> bool:
    """Claim one minion + dispatch + complete/fail. Returns True if work done."""
    m = q.claim_next(worker_id, kinds=kinds, lease_seconds=300)
    if m is None:
        return False
    kind = m.get("kind")
    mid = int(m["id"])
    handler = _HANDLERS.get(kind)
    if handler is None:
        q.fail(mid, f"no_handler_for_kind:{kind}", retry=False)
        return True
    payload = m.get("payload") or {}
    if isinstance(payload, str):
        try:
            import json as _json
            payload = _json.loads(payload)
        except Exception:
            payload = {}
    t0 = time.time()
    try:
        eng = _engine()
        _slug = payload.get("project_slug") or payload.get("slug") if isinstance(payload, dict) else None
        with trace_span(f"cron.minion.{kind}", kind="cron", project_slug=_slug):
            result = handler(payload, eng) or {}
        result.setdefault("worker_id", worker_id)
        result.setdefault("elapsed_ms", int((time.time() - t0) * 1000))
        q.complete(mid, result)
    except Exception as e:
        err = f"{e.__class__.__name__}: {e}\n{traceback.format_exc()[:1500]}"
        logger.exception("minion %s failed", mid)
        q.fail(mid, err, retry=True)
    return True


# ── Consumer loop ─────────────────────────────────────────────────
#
# `run_one()` claims + dispatches a single job and returns. Nothing in the app
# drove it on a loop, so pending minions (autosim_generate_grounded, dream_lite,
# …) piled up forever. This loop is the missing consumer: it polls the durable
# Postgres queue, dispatches via the handler registry above, and is fully
# fail-soft — one bad job (or even a DB blip) never kills the loop.

_STOP_EVENT = threading.Event()
_WORKER_THREAD: Optional[threading.Thread] = None


def run_worker_loop(
    worker_id: str = "minion-worker",
    kinds: Optional[List[str]] = None,
    idle_sleep: float = 3.0,
) -> None:
    """Continuously claim + dispatch pending minions until stopped.

    Drains as fast as the queue has work (claim returns True → immediately try
    the next), then backs off `idle_sleep` seconds when the queue is empty.
    Fully fail-soft: any exception in a cycle is logged and the loop continues.
    """
    logger.info(
        "minion consumer started (worker_id=%s, kinds=%s, %d handlers registered)",
        worker_id,
        kinds or "ALL",
        len(_HANDLERS),
    )
    while not _STOP_EVENT.is_set():
        did_work = False
        try:
            did_work = run_one(worker_id=worker_id, kinds=kinds)
        except Exception:
            # claim/dispatch/complete already swallow their own errors, but
            # guard the loop body anyway (e.g. transient DB connection loss).
            logger.exception("minion consumer cycle crashed (continuing)")
        if not did_work:
            _STOP_EVENT.wait(idle_sleep)


def start_worker(
    worker_id: str = "minion-worker",
    kinds: Optional[List[str]] = None,
    idle_sleep: float = 3.0,
) -> None:
    """Idempotent. Spawn the consumer in a daemon thread if not already running.

    Sync/blocking by design (run_one + handlers do their own thread/loop
    juggling via _run_async_safely), so it runs in a thread rather than an
    asyncio task. Opt-out via MINION_WORKER_DISABLED=1.
    """
    global _WORKER_THREAD
    import os
    if os.getenv("MINION_WORKER_DISABLED", "").lower() in ("1", "true", "yes"):
        logger.info("minion consumer disabled via env")
        return
    if _WORKER_THREAD and _WORKER_THREAD.is_alive():
        return
    _STOP_EVENT.clear()
    _WORKER_THREAD = threading.Thread(
        target=run_worker_loop,
        args=(worker_id, kinds, idle_sleep),
        daemon=True,
        name="minion-consumer",
    )
    _WORKER_THREAD.start()


def stop_worker() -> None:
    _STOP_EVENT.set()
