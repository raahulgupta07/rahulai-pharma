"""Native observability / tracing layer for Dash.

Lightweight, fail-soft span tracing backed by ``public.dash_traces``.

Spans form a tree via contextvars: a root trace (``start_trace``) sets the
current trace_id; nested ``@trace_step`` / ``trace_span`` calls become children
of whatever span is currently open. Async-safe — contextvars propagate across
``await``.

Every DB write is wrapped in try/except and swallowed. Tracing must NEVER break
or slow-fail the wrapped function. Kill-switch: env ``TRACING_DISABLED`` truthy.

Writes go through ``db.session.get_write_engine()`` (read-write, search_path
public,dash). JSONB binds use ``CAST(:meta AS jsonb)`` — never the ``:x::jsonb``
form (collides with SQLAlchemy named params).
"""

from __future__ import annotations

import contextvars
import functools
import inspect
import logging
import os
import time
import uuid
from contextlib import contextmanager

from sqlalchemy import text

logger = logging.getLogger("dash.obs.trace")

__all__ = [
    "trace_step",
    "trace_span",
    "start_trace",
    "end_trace",
    "record_cost",
    "set_project",
    "set_root_meta",
]

# ── Context state ─────────────────────────────────────────────────────────────
_trace_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("dash_trace_id", default=None)
_parent_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("dash_span_parent", default=None)
_kind: contextvars.ContextVar[str | None] = contextvars.ContextVar("dash_trace_kind", default=None)
_project: contextvars.ContextVar[str | None] = contextvars.ContextVar("dash_trace_project", default=None)
# DB id of the currently-open span (for record_cost).
_cur_db_id: contextvars.ContextVar[int | None] = contextvars.ContextVar("dash_cur_db_id", default=None)
# DB id + monotonic start of the ROOT span (set by start_trace, closed by end_trace).
_root_db_id: contextvars.ContextVar[int | None] = contextvars.ContextVar("dash_root_db_id", default=None)
_root_start: contextvars.ContextVar[float | None] = contextvars.ContextVar("dash_root_start", default=None)


def _disabled() -> bool:
    return str(os.getenv("TRACING_DISABLED", "")).strip().lower() in ("1", "true", "yes")


def _new_id() -> str:
    return uuid.uuid4().hex


# ── DB helpers (all fail-soft) ──────────────────────────────────────────────────
def _insert_span(name: str, kind: str, parent_id: str | None, trace_id: str,
                 project_slug: str | None, meta: dict | None) -> int | None:
    """Insert a running span. Returns its DB id, or None on any failure."""
    try:
        from db.session import get_write_engine
        eng = get_write_engine()
        import json
        meta_json = json.dumps(meta) if meta is not None else None
        with eng.begin() as conn:
            row = conn.execute(
                text(
                    "INSERT INTO public.dash_traces "
                    "(trace_id, parent_id, name, kind, project_slug, status, meta) "
                    "VALUES (:trace_id, :parent_id, :name, :kind, :project_slug, 'running', "
                    "CAST(:meta AS jsonb)) RETURNING id"
                ),
                {
                    "trace_id": trace_id,
                    "parent_id": parent_id,
                    "name": name[:500],
                    "kind": kind[:64],
                    "project_slug": project_slug,
                    "meta": meta_json,
                },
            ).first()
            return int(row[0]) if row else None
    except Exception as e:  # noqa: BLE001 — fail-soft
        logger.debug("trace insert failed: %s", e)
        return None


def _finish_span(db_id: int | None, status: str, duration_ms: int, error: str | None) -> None:
    if db_id is None:
        return
    try:
        from db.session import get_write_engine
        eng = get_write_engine()
        with eng.begin() as conn:
            conn.execute(
                text(
                    "UPDATE public.dash_traces SET status = :status, "
                    "duration_ms = :duration_ms, finished_at = now(), error = :error "
                    "WHERE id = :id"
                ),
                {"status": status, "duration_ms": duration_ms,
                 "error": (error[:2000] if error else None), "id": db_id},
            )
    except Exception as e:  # noqa: BLE001
        logger.debug("trace finish failed: %s", e)


# ── Public API ──────────────────────────────────────────────────────────────────
def start_trace(kind: str, project_slug: str | None = None, name: str | None = None) -> str:
    """Begin a ROOT trace. Sets contextvars and returns the trace_id (uuid hex)."""
    tid = _new_id()
    if _disabled():
        return tid
    _trace_id.set(tid)
    _kind.set(kind)
    _parent_id.set(None)
    _cur_db_id.set(None)
    if project_slug is not None:
        _project.set(project_slug)
    # The root trace itself is recorded as a span so it appears in queries.
    db_id = _insert_span(name or kind, kind, None, tid, project_slug or _project.get(), None)
    _cur_db_id.set(db_id)
    _root_db_id.set(db_id)
    _root_start.set(time.monotonic())
    return tid


def end_trace(status: str = "done", error: str | None = None) -> None:
    """Close the CURRENT root span (the row inserted by start_trace).

    Updates status, finished_at, duration_ms (from the root's started_at) and
    error if given, then clears the trace contextvars so a subsequent
    start_trace begins fresh. Fail-soft; no-op when disabled or no root open.
    """
    if _disabled():
        return
    db_id = _root_db_id.get()
    start = _root_start.get()
    if db_id is not None:
        dur = int((time.monotonic() - start) * 1000) if start is not None else 0
        _finish_span(db_id, status, dur, error)
    # Clear trace contextvars so the next start_trace is a clean root.
    _trace_id.set(None)
    _kind.set(None)
    _parent_id.set(None)
    _cur_db_id.set(None)
    _root_db_id.set(None)
    _root_start.set(None)


def set_project(slug: str) -> None:
    """Set project_slug on the current contextvar for subsequent spans."""
    if _disabled():
        return
    _project.set(slug)


def record_cost(usd: float | None = None, tokens: int | None = None,
                model: str | None = None) -> None:
    """Attach cost/tokens to the CURRENT span. No-op if none open. When `model`
    is given, also stamp meta.model (so the Usage dashboard can break cost down
    by model). Merges into existing meta — never clobbers."""
    if _disabled():
        return
    db_id = _cur_db_id.get()
    if db_id is None:
        return
    try:
        from db.session import get_write_engine
        eng = get_write_engine()
        with eng.begin() as conn:
            if model:
                conn.execute(
                    text(
                        "UPDATE public.dash_traces SET "
                        "cost_usd = COALESCE(cost_usd, 0) + COALESCE(:usd, 0), "
                        "tokens = COALESCE(tokens, 0) + COALESCE(:tokens, 0), "
                        "meta = COALESCE(meta, '{}'::jsonb) "
                        "       || jsonb_build_object('model', :model::text) "
                        "WHERE id = :id"
                    ),
                    {"usd": usd, "tokens": tokens, "model": model, "id": db_id},
                )
            else:
                conn.execute(
                    text(
                        "UPDATE public.dash_traces SET "
                        "cost_usd = COALESCE(cost_usd, 0) + COALESCE(:usd, 0), "
                        "tokens = COALESCE(tokens, 0) + COALESCE(:tokens, 0) "
                        "WHERE id = :id"
                    ),
                    {"usd": usd, "tokens": tokens, "id": db_id},
                )
    except Exception as e:  # noqa: BLE001
        logger.debug("record_cost failed: %s", e)


def set_root_meta(**kw) -> None:
    """Merge attributes (actor, channel, store_id, …) into the ROOT span's meta.

    Powers the Usage dashboard: lets the chat handler tag each run with who ran
    it (actor), how it came in (channel: web|api), and which store (api keys).
    Drops None values; merges, never clobbers. Fail-soft."""
    if _disabled():
        return
    db_id = _root_db_id.get()
    if db_id is None:
        return
    attrs = {k: v for k, v in kw.items() if v is not None}
    if not attrs:
        return
    try:
        import json
        from db.session import get_write_engine
        eng = get_write_engine()
        with eng.begin() as conn:
            conn.execute(
                text(
                    "UPDATE public.dash_traces SET "
                    "meta = COALESCE(meta, '{}'::jsonb) || CAST(:attrs AS jsonb) "
                    "WHERE id = :id"
                ),
                {"attrs": json.dumps(attrs), "id": db_id},
            )
    except Exception as e:  # noqa: BLE001
        logger.debug("set_root_meta failed: %s", e)


@contextmanager
def trace_span(name: str, kind: str = "task", project_slug: str | None = None,
               meta: dict | None = None):
    """Sync context manager yielding a span handle (its DB id, may be None)."""
    if _disabled():
        yield None
        return

    tid = _trace_id.get() or _new_id()
    if _trace_id.get() is None:
        _trace_id.set(tid)
    parent = _cur_db_id.get()
    parent_trace = _parent_id.get()
    eff_project = project_slug or _project.get()

    db_id = _insert_span(name, kind, str(parent) if parent is not None else parent_trace,
                         tid, eff_project, meta)
    prev_cur = _cur_db_id.get()
    _cur_db_id.set(db_id)
    start = time.monotonic()
    status, err = "done", None
    try:
        yield db_id
    except Exception as e:  # noqa: BLE001 — record + re-raise
        status, err = "error", str(e)
        raise
    finally:
        dur = int((time.monotonic() - start) * 1000)
        _finish_span(db_id, status, dur, err)
        # Restore via set(prev) — NOT reset(token): a token created in one
        # context can't be reset in another (SSE generators / to_thread run in
        # a different context → reset() raises). set(prev) is context-safe.
        _cur_db_id.set(prev_cur)


def trace_step(name: str, kind: str = "task"):
    """Decorator that times a sync OR async function as a child span.

    On success status='done'; on exception status='error' + error, then re-raise.
    """
    def decorator(fn):
        if _disabled():
            return fn

        if inspect.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def awrapper(*args, **kwargs):
                tid = _trace_id.get() or _new_id()
                if _trace_id.get() is None:
                    _trace_id.set(tid)
                parent = _cur_db_id.get()
                db_id = _insert_span(name, kind,
                                     str(parent) if parent is not None else _parent_id.get(),
                                     tid, _project.get(), None)
                prev = _cur_db_id.get()
                _cur_db_id.set(db_id)
                start = time.monotonic()
                status, err = "done", None
                try:
                    return await fn(*args, **kwargs)
                except Exception as e:  # noqa: BLE001
                    status, err = "error", str(e)
                    raise
                finally:
                    _finish_span(db_id, status, int((time.monotonic() - start) * 1000), err)
                    _cur_db_id.set(prev)
            return awrapper

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            tid = _trace_id.get() or _new_id()
            if _trace_id.get() is None:
                _trace_id.set(tid)
            parent = _cur_db_id.get()
            db_id = _insert_span(name, kind,
                                 str(parent) if parent is not None else _parent_id.get(),
                                 tid, _project.get(), None)
            prev = _cur_db_id.get()
            _cur_db_id.set(db_id)
            start = time.monotonic()
            status, err = "done", None
            try:
                return fn(*args, **kwargs)
            except Exception as e:  # noqa: BLE001
                status, err = "error", str(e)
                raise
            finally:
                _finish_span(db_id, status, int((time.monotonic() - start) * 1000), err)
                _cur_db_id.set(prev)
        return wrapper

    return decorator
