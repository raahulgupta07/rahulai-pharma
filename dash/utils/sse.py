"""Server-Sent Events (SSE) event emitter — single source of truth.

Every new SSE generator MUST use this. Wraps `safe_dumps` in try/except so
a JSON-serialization failure (Decimal/UUID/datetime/numpy/bytes in payload)
never closes the stream silently. On TypeError, emits a sentinel
`event: <name>_error\\ndata: {}\\n\\n` instead so frontend can show a degraded
state while remaining events keep flowing.

USAGE:
    # Async generator (FastAPI streaming endpoint):
    from dash.utils.sse import emit_event
    yield await emit_event("ToolCallCompleted", payload)

    # Sync generator (used inside sync functions / blocking generators):
    from dash.utils.sse import emit_event_sync
    yield emit_event_sync("ReasoningStep", payload)

Both return the formatted SSE string `event: NAME\\ndata: JSON\\n\\n`.

PR RULE: NEVER hand-format SSE strings with `f"event: X\\ndata: {json.dumps(...)}"`.
Always use these helpers — the TypeError safety net protects every endpoint.

PHASE 7 — SSE AUDIT: on every successful emit, fire-and-forget a row into
public.dash_sse_audit via a small ThreadPoolExecutor (background, fail-soft,
never raises, never blocks the emit). Disable with SSE_AUDIT_DISABLED=1.
"""
from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor

from dash.utils import safe_dumps

logger = logging.getLogger(__name__)

# Module-level pool — 2 workers is plenty: audit inserts are tiny and we
# never want SSE emit to back up on DB latency. Daemon threads so this
# never blocks process exit.
_AUDIT_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="sse-audit")
_AUDIT_DISABLED = os.getenv("SSE_AUDIT_DISABLED", "").lower() in ("1", "true", "yes")


def _format_error_event(name: str) -> str:
    """Sentinel emitted when payload serialization fails. Frontend can listen
    for `*_error` event suffix to show degraded state without crashing."""
    return f"event: {name}_error\ndata: {{}}\n\n"


def _audit_write(
    session_id: str | None,
    event_name: str,
    bytes_emitted: int | None,
    error: str | None,
    project_slug: str | None,
) -> None:
    """Background DB write. Fail-soft — swallow every exception so audit
    failures never propagate to the emit caller."""
    try:
        from sqlalchemy import text

        from db.session import get_write_engine

        with get_write_engine().begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO public.dash_sse_audit "
                    "(session_id, event_name, bytes_emitted, error, project_slug) "
                    "VALUES (:sid, :ev, :b, :err, :ps)"
                ),
                {
                    "sid": session_id,
                    "ev": event_name,
                    "b": bytes_emitted,
                    "err": error,
                    "ps": project_slug,
                },
            )
    except Exception:  # noqa: BLE001 — audit must never raise
        # Don't even log at warning — this fires per-event and would flood logs
        # if the audit table is missing. Migration 144 creates it.
        pass


def _audit_emit_async(
    session_id: str | None,
    event_name: str,
    bytes_emitted: int | None,
    error: str | None,
    project_slug: str | None,
) -> None:
    """Submit audit row write to background pool. Never raises."""
    if _AUDIT_DISABLED:
        return
    try:
        _AUDIT_POOL.submit(
            _audit_write,
            session_id,
            event_name,
            bytes_emitted,
            error,
            project_slug,
        )
    except Exception:  # noqa: BLE001 — pool full/shutdown: drop the audit
        pass


def emit_event_sync(
    name: str,
    payload,
    *,
    session_id: str | None = None,
    project_slug: str | None = None,
) -> str:
    """Sync SSE event formatter. Use inside sync generators (`def gen():`).

    Args:
        name: SSE event name (e.g. "ToolCallStarted", "TeamRunContent").
        payload: any JSON-encodable object (handled by safe_dumps —
                 Decimal/UUID/datetime/numpy/bytes all coerce).
        session_id: optional chat session id for audit attribution.
        project_slug: optional project slug for audit attribution.

    Returns:
        Formatted SSE string. On TypeError → sentinel `<name>_error` event with
        empty data, logged at WARNING. Stream NEVER dies.
    """
    try:
        body = safe_dumps(payload)
        out = f"event: {name}\ndata: {body}\n\n"
        _audit_emit_async(session_id, name, len(body), None, project_slug)
        return out
    except TypeError as exc:
        logger.warning(
            "SSE emit %s failed (TypeError): %s — emitting %s_error sentinel",
            name, exc, name,
        )
        _audit_emit_async(session_id, name, None, f"TypeError: {exc}", project_slug)
        return _format_error_event(name)
    except Exception as exc:  # noqa: BLE001 — even non-TypeError must not kill the stream
        logger.warning(
            "SSE emit %s failed (%s): %s — emitting %s_error sentinel",
            name, type(exc).__name__, exc, name,
        )
        _audit_emit_async(
            session_id, name, None, f"{type(exc).__name__}: {exc}", project_slug,
        )
        return _format_error_event(name)


async def emit_event(
    name: str,
    payload,
    *,
    session_id: str | None = None,
    project_slug: str | None = None,
) -> str:
    """Async wrapper around `emit_event_sync` for use in `async def` generators.

    Pure CPU work (no I/O); the `async` is for ergonomic `yield await emit_event(...)`
    in async streaming endpoints. Same fail-soft semantics as the sync version:
    every new SSE generator MUST use this so a bad payload type can never kill
    the stream before TeamRunContent is emitted.
    """
    return emit_event_sync(name, payload, session_id=session_id, project_slug=project_slug)
