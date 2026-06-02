"""
Human-in-the-Loop (HITL) framework for Dash agentic platform.

Three decorators wrap agent tool functions to gate execution on human approval,
collect structured user input, or hand off to an external executor.

Behind ``EXPERIMENTAL_AGI=1`` env flag — when disabled, decorators are no-ops
(they call the wrapped fn directly with no gating).

Storage: ``dash.dash_hitl_pending`` (migration 039).
In-process coordination: ``_pending_queues`` dict of asyncio.Queue keyed by run_id.
"""
from __future__ import annotations

import asyncio
import functools
import inspect
import json
import logging
import os
import uuid
from contextvars import ContextVar
from typing import Any, Callable, Dict, Optional, Type

from sqlalchemy import text

logger = logging.getLogger(__name__)

# ── ContextVars set by chat endpoint middleware ─────────────────────────
current_run_id: ContextVar[Optional[str]] = ContextVar("current_run_id", default=None)
current_project_slug: ContextVar[Optional[str]] = ContextVar("current_project_slug", default=None)
current_user_id: ContextVar[Optional[int]] = ContextVar("current_user_id", default=None)
current_agent_name: ContextVar[Optional[str]] = ContextVar("current_agent_name", default=None)

# ── In-process registry: run_id → asyncio.Queue ─────────────────────────
_pending_queues: Dict[str, asyncio.Queue] = {}
_registry_lock: Optional[asyncio.Lock] = None  # lazy-init (Py3.9 compat)


def _lock() -> asyncio.Lock:
    global _registry_lock
    if _registry_lock is None:
        _registry_lock = asyncio.Lock()
    return _registry_lock

# Default timeout (seconds) for awaiting human response
DEFAULT_TIMEOUT_S = 300.0


def _enabled() -> bool:
    return os.getenv("EXPERIMENTAL_AGI") == "1"


def _get_run_id() -> str:
    rid = current_run_id.get()
    if not rid:
        rid = uuid.uuid4().hex
    return rid


def _get_engine():
    """Lazy import to avoid circular deps and keep decorators import-light."""
    try:
        from db.session import get_sql_engine  # type: ignore
        return get_sql_engine()
    except Exception:
        try:
            from db import get_sql_engine  # type: ignore
            return get_sql_engine()
        except Exception:
            from db import db_url  # type: ignore
            from sqlalchemy import create_engine
            from sqlalchemy.pool import NullPool
            return create_engine(db_url, poolclass=NullPool)


async def _register_queue(run_id: str) -> asyncio.Queue:
    async with _lock():
        q = _pending_queues.get(run_id)
        if q is None:
            q = asyncio.Queue(maxsize=4)
            _pending_queues[run_id] = q
        return q


async def _drop_queue(run_id: str) -> None:
    async with _lock():
        _pending_queues.pop(run_id, None)


def get_pending_queue(run_id: str) -> Optional[asyncio.Queue]:
    """Public helper used by app/hitl_api.py to push responses into the queue."""
    return _pending_queues.get(run_id)


def _insert_pending(
    run_id: str,
    agent_name: str,
    action_type: str,
    payload: Dict[str, Any],
    project_slug: Optional[str],
    user_id: Optional[int],
) -> None:
    eng = _get_engine()
    with eng.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO dash.dash_hitl_pending
                  (run_id, project_slug, user_id, agent_name, action_type, payload, status)
                VALUES (:rid, :ps, :uid, :ag, :at, CAST(:pl AS jsonb), 'pending')
                ON CONFLICT (run_id) DO UPDATE
                  SET payload = EXCLUDED.payload,
                      status = 'pending',
                      created_at = now(),
                      expires_at = now() + INTERVAL '5 minutes',
                      responded_at = NULL,
                      responded_by = NULL,
                      response = NULL
                """
            ),
            {
                "rid": run_id,
                "ps": project_slug,
                "uid": user_id,
                "ag": agent_name,
                "at": action_type,
                "pl": json.dumps(payload, default=str),
            },
        )


def _mark_expired(run_id: str) -> None:
    try:
        eng = _get_engine()
        with eng.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE dash.dash_hitl_pending
                       SET status = 'expired', responded_at = now()
                     WHERE run_id = :rid AND status = 'pending'
                    """
                ),
                {"rid": run_id},
            )
    except Exception:
        logger.exception("hitl: failed to mark expired run_id=%s", run_id)


async def _await_response(run_id: str, timeout: float) -> Dict[str, Any]:
    """Await a single response message for ``run_id``.

    Returns the dict pushed by app/hitl_api.py, or raises asyncio.TimeoutError.
    """
    q = await _register_queue(run_id)
    try:
        msg = await asyncio.wait_for(q.get(), timeout=timeout)
        return msg
    finally:
        await _drop_queue(run_id)


async def _maybe_call(fn: Callable, *args, **kwargs):
    """Invoke fn whether sync or async."""
    if inspect.iscoroutinefunction(fn):
        return await fn(*args, **kwargs)
    return fn(*args, **kwargs)


# ── 1. require_confirmation ─────────────────────────────────────────────
def require_confirmation(
    action_type: str,
    message_template: str = "",
    *,
    timeout: float = DEFAULT_TIMEOUT_S,
):
    """Decorator: gate tool execution on human approval.

    On call, inserts a row in dash_hitl_pending with status='pending', awaits a
    response via the in-process queue. If approved → invokes wrapped fn.
    If rejected/expired → returns {"ok": False, "reason": ...}.

    Soft-disabled when EXPERIMENTAL_AGI != "1": pass-through to wrapped fn.

    ``message_template`` may use kwargs from the call (str.format).
    """

    def decorator(fn: Callable):
        if not _enabled():
            return fn

        @functools.wraps(fn)
        async def async_wrapper(*args, **kwargs):
            run_id = _get_run_id()
            agent = current_agent_name.get() or fn.__module__
            project_slug = current_project_slug.get()
            user_id = current_user_id.get()
            try:
                msg = message_template.format(**kwargs) if message_template else ""
            except Exception:
                msg = message_template
            payload = {
                "tool": fn.__name__,
                "args": list(args),
                "kwargs": {k: (v if _json_safe(v) else repr(v)) for k, v in kwargs.items()},
                "message": msg,
                "action_type": action_type,
            }
            try:
                _insert_pending(run_id, agent, "confirmation", payload, project_slug, user_id)
            except Exception:
                logger.exception("hitl: insert_pending failed; falling through")
                return await _maybe_call(fn, *args, **kwargs)

            try:
                resp = await _await_response(run_id, timeout=timeout)
            except asyncio.TimeoutError:
                _mark_expired(run_id)
                return {"ok": False, "reason": "expired"}

            decision = (resp or {}).get("decision")
            if decision == "approve":
                return await _maybe_call(fn, *args, **kwargs)
            return {"ok": False, "reason": "user_rejected", "note": (resp or {}).get("note")}

        @functools.wraps(fn)
        def sync_wrapper(*args, **kwargs):
            # If called from sync context, run our async path in a fresh loop.
            return asyncio.run(async_wrapper(*args, **kwargs))

        return async_wrapper if inspect.iscoroutinefunction(fn) else sync_wrapper

    return decorator


# ── 2. require_user_input ───────────────────────────────────────────────
def require_user_input(schema: Type, *, timeout: float = DEFAULT_TIMEOUT_S):
    """Decorator: emit a structured question, await user form response.

    Parses the response.data into ``schema`` (a pydantic.BaseModel subclass) and
    passes it as the first arg to the wrapped fn (after self if present).

    The wrapped fn signature is expected to receive ``parsed`` followed by the
    original args/kwargs.

    Soft-disabled when EXPERIMENTAL_AGI != "1": calls fn(None, *args, **kwargs).
    """
    schema_payload = _schema_to_dict(schema)

    def decorator(fn: Callable):
        if not _enabled():
            @functools.wraps(fn)
            def passthrough(*args, **kwargs):
                return fn(None, *args, **kwargs)
            return passthrough

        @functools.wraps(fn)
        async def async_wrapper(*args, **kwargs):
            run_id = _get_run_id()
            agent = current_agent_name.get() or fn.__module__
            project_slug = current_project_slug.get()
            user_id = current_user_id.get()
            payload = {
                "tool": fn.__name__,
                "schema": schema_payload,
                "prompt": (fn.__doc__ or "").strip().splitlines()[0] if fn.__doc__ else "",
            }
            try:
                _insert_pending(run_id, agent, "user_input", payload, project_slug, user_id)
            except Exception:
                logger.exception("hitl: insert_pending failed; falling through")
                return await _maybe_call(fn, None, *args, **kwargs)

            try:
                resp = await _await_response(run_id, timeout=timeout)
            except asyncio.TimeoutError:
                _mark_expired(run_id)
                return {"ok": False, "reason": "expired"}

            data = (resp or {}).get("data") or {}
            try:
                parsed = schema(**data) if hasattr(schema, "__fields__") or hasattr(schema, "model_fields") else data
            except Exception as e:
                return {"ok": False, "reason": "invalid_input", "error": str(e)}

            return await _maybe_call(fn, parsed, *args, **kwargs)

        @functools.wraps(fn)
        def sync_wrapper(*args, **kwargs):
            return asyncio.run(async_wrapper(*args, **kwargs))

        return async_wrapper if inspect.iscoroutinefunction(fn) else sync_wrapper

    return decorator


# ── 3. external_execution ───────────────────────────────────────────────
def external_execution(action_manifest_fn: Optional[Callable] = None, *, timeout: float = DEFAULT_TIMEOUT_S):
    """Decorator: emit a manifest, wait for an external POST to deliver result.

    ``action_manifest_fn(*args, **kwargs) -> dict`` (optional) builds the
    manifest payload that's stored under dash_hitl_pending.payload.manifest.
    If omitted, defaults to {"tool": fn.__name__, "args": ..., "kwargs": ...}.

    The wrapped fn is NEVER invoked — the external system runs the action and
    posts the result back via /api/hitl/{run_id}/external-result.

    Returns {"ok": True, "result": <result>} on success, or
    {"ok": False, "reason": "expired"} on timeout.

    Soft-disabled when EXPERIMENTAL_AGI != "1": calls wrapped fn directly.
    """
    # Allow @external_execution with no args
    if callable(action_manifest_fn) and not _is_manifest_fn(action_manifest_fn):
        # Used as bare decorator: @external_execution
        fn = action_manifest_fn
        return external_execution(None, timeout=timeout)(fn)

    def decorator(fn: Callable):
        if not _enabled():
            return fn

        @functools.wraps(fn)
        async def async_wrapper(*args, **kwargs):
            run_id = _get_run_id()
            agent = current_agent_name.get() or fn.__module__
            project_slug = current_project_slug.get()
            user_id = current_user_id.get()
            try:
                manifest = (
                    action_manifest_fn(*args, **kwargs)
                    if action_manifest_fn
                    else {"tool": fn.__name__, "args": list(args), "kwargs": dict(kwargs)}
                )
            except Exception as e:
                manifest = {"tool": fn.__name__, "error_building_manifest": str(e)}
            payload = {
                "tool": fn.__name__,
                "manifest": manifest,
            }
            try:
                _insert_pending(run_id, agent, "external_execution", payload, project_slug, user_id)
            except Exception:
                logger.exception("hitl: insert_pending failed; falling through")
                return await _maybe_call(fn, *args, **kwargs)

            try:
                resp = await _await_response(run_id, timeout=timeout)
            except asyncio.TimeoutError:
                _mark_expired(run_id)
                return {"ok": False, "reason": "expired"}

            return {"ok": True, "result": (resp or {}).get("result")}

        @functools.wraps(fn)
        def sync_wrapper(*args, **kwargs):
            return asyncio.run(async_wrapper(*args, **kwargs))

        return async_wrapper if inspect.iscoroutinefunction(fn) else sync_wrapper

    return decorator


# ── helpers ─────────────────────────────────────────────────────────────
def _json_safe(v: Any) -> bool:
    try:
        json.dumps(v, default=str)
        return True
    except Exception:
        return False


def _is_manifest_fn(fn: Callable) -> bool:
    """Heuristic: a manifest fn is something named like build_*/manifest_* or
    explicitly tagged. If we can't tell, assume yes (safer to treat as
    factory than to swallow a tool)."""
    name = getattr(fn, "__name__", "")
    if name.startswith("_") or name in ("manifest", "build_manifest"):
        return True
    if "manifest" in name.lower():
        return True
    # If the callable has zero or one positional params, treat as a tool;
    # otherwise treat as a manifest factory.
    try:
        sig = inspect.signature(fn)
        params = [p for p in sig.parameters.values() if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
        return len(params) >= 1 and not name  # ambiguous; default to tool
    except Exception:
        return False


def _schema_to_dict(schema: Type) -> Dict[str, Any]:
    """Best-effort serialize a pydantic model class to a JSON-renderable dict."""
    try:
        if hasattr(schema, "model_json_schema"):
            return schema.model_json_schema()  # pydantic v2
        if hasattr(schema, "schema"):
            return schema.schema()  # pydantic v1
    except Exception:
        pass
    return {"title": getattr(schema, "__name__", "Input"), "type": "object", "properties": {}}
