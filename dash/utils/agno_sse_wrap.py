"""Audit wrapper around Agno team streams.

Every event yielded by team.arun_stream() lands one row in dash_sse_audit
with event_name + bytes_emitted + optional error. Original event still
yields to the SSE response intact — audit is best-effort, never blocks.
"""
from __future__ import annotations
import logging
from dash.utils.sse import _audit_emit_async
from dash.utils.safe_json import safe_dumps

logger = logging.getLogger(__name__)


def _audit_event(event, sid: str, project_slug: str) -> None:
    """Best-effort audit of a single Agno stream event. Never raises."""
    name = getattr(event, "event", None) or type(event).__name__
    # Prometheus: increment sse_events_total{type=<name>} per event (best-effort).
    try:
        from dash.utils.metrics import inc_sse as _inc_sse
        _inc_sse(name)
    except Exception:
        pass
    try:
        if hasattr(event, "to_dict"):
            payload_str = safe_dumps(event.to_dict())
        elif hasattr(event, "model_dump"):
            payload_str = safe_dumps(event.model_dump())
        else:
            payload_str = safe_dumps(getattr(event, "__dict__", {"repr": repr(event)}))
        _audit_emit_async(sid, name, len(payload_str), None, project_slug)
    except Exception as exc:
        _audit_emit_async(sid, name, 0, str(exc)[:200], project_slug)
        logger.debug("agno audit emit failed: %s", exc)


async def audited_team_stream(team, *, session_id: str, project_slug: str, **run_kwargs):
    """Wrap team.arun_stream() — audit every yielded event (async)."""
    sid = session_id or ""
    try:
        gen = team.arun_stream(**run_kwargs)
    except Exception as exc:
        _audit_emit_async(sid, "StreamInitError", 0, str(exc)[:200], project_slug)
        raise
    async for event in gen:
        _audit_event(event, sid, project_slug)
        yield event


def audited_team_stream_sync(team, *args, session_id: str, project_slug: str, **run_kwargs):
    """Wrap team.run(stream=True) sync iterator — audit every yielded event.

    `session_id` is used both for audit attribution AND auto-forwarded to
    team.run() as session_id kwarg. Other kwargs forwarded verbatim.
    """
    sid = session_id or ""
    if sid and "session_id" not in run_kwargs:
        run_kwargs["session_id"] = sid
    try:
        it = team.run(*args, **run_kwargs)
    except Exception as exc:
        _audit_emit_async(sid, "StreamInitError", 0, str(exc)[:200], project_slug)
        raise
    for event in it:
        _audit_event(event, sid, project_slug)
        yield event
