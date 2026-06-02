"""Per-session per-agent mutable scratchpad.

Use cases: Taskboard agent CRUD on todos, Analyst tracking SQL retry
attempts, Engineer remembering view names created this session.

Distinct from dash_memories (long-term) and Agno session_state (ephemeral,
in-process). This is session-scoped, persisted, agent-mutable.
"""
from __future__ import annotations

import json as _json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


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


def set_state(session_id: str, agent_name: str, key: str, value: Any) -> bool:
    eng = _get_engine()
    if eng is None:
        return False
    try:
        from sqlalchemy import text
        with eng.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO dash.dash_agentic_state
                      (session_id, agent_name, key, value)
                    VALUES (:sid, :ag, :k, CAST(:v AS jsonb))
                    ON CONFLICT (session_id, agent_name, key) DO UPDATE
                      SET value = EXCLUDED.value, updated_at = now()
                    """
                ),
                {"sid": session_id, "ag": agent_name, "k": key,
                 "v": _json.dumps(value, default=str)},
            )
        return True
    except Exception as e:
        logger.warning("set_state failed: %s", e)
        return False


def get_state(session_id: str, agent_name: str, key: str) -> Any:
    eng = _get_engine()
    if eng is None:
        return None
    try:
        from sqlalchemy import text
        with eng.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT value FROM dash.dash_agentic_state "
                    "WHERE session_id=:sid AND agent_name=:ag AND key=:k"
                ),
                {"sid": session_id, "ag": agent_name, "k": key},
            ).first()
        return row[0] if row else None
    except Exception:
        return None


def list_state(session_id: str, agent_name: Optional[str] = None) -> Dict[str, Any]:
    eng = _get_engine()
    if eng is None:
        return {}
    try:
        from sqlalchemy import text
        with eng.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT agent_name, key, value, updated_at FROM dash.dash_agentic_state "
                    "WHERE session_id=:sid AND (:ag IS NULL OR agent_name=:ag) "
                    "ORDER BY agent_name, key"
                ),
                {"sid": session_id, "ag": agent_name},
            ).mappings().all()
        # group by agent_name
        out: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            out.setdefault(r["agent_name"], {})[r["key"]] = r["value"]
        return out
    except Exception:
        return {}


def delete_state(session_id: str, agent_name: str, key: str) -> bool:
    eng = _get_engine()
    if eng is None:
        return False
    try:
        from sqlalchemy import text
        with eng.begin() as conn:
            r = conn.execute(
                text(
                    "DELETE FROM dash.dash_agentic_state "
                    "WHERE session_id=:sid AND agent_name=:ag AND key=:k"
                ),
                {"sid": session_id, "ag": agent_name, "k": key},
            )
        return r.rowcount > 0
    except Exception:
        return False


def clear_session(session_id: str) -> int:
    """Drop all state for a session. Returns rows deleted."""
    eng = _get_engine()
    if eng is None:
        return 0
    try:
        from sqlalchemy import text
        with eng.begin() as conn:
            r = conn.execute(
                text("DELETE FROM dash.dash_agentic_state WHERE session_id=:sid"),
                {"sid": session_id},
            )
        return r.rowcount or 0
    except Exception:
        return 0


# ── Agno @tool wrappers (Taskboard-style) ───────────────────────────────
def _try_tool(fn):
    try:
        from agno.tools import tool
        return tool(fn)
    except Exception:
        return fn


def _ctx_session() -> tuple:
    try:
        from dash.agentic.hooks import current_run_id, current_agent_name
        # use run_id as session_id when proper session unavailable
        return (current_run_id.get() or "default", current_agent_name.get() or "unknown")
    except Exception:
        return ("default", "unknown")


@_try_tool
def state_set(key: str, value: Any) -> Dict[str, Any]:
    """Save a value to per-session per-agent scratchpad."""
    sid, ag = _ctx_session()
    ok = set_state(sid, ag, key, value)
    return {"ok": ok, "session_id": sid, "agent": ag, "key": key}


@_try_tool
def state_get(key: str) -> Dict[str, Any]:
    """Retrieve a value from session scratchpad."""
    sid, ag = _ctx_session()
    val = get_state(sid, ag, key)
    return {"ok": True, "value": val, "key": key}


@_try_tool
def state_list() -> Dict[str, Any]:
    """List all scratchpad entries for current session."""
    sid, ag = _ctx_session()
    return {"ok": True, "state": list_state(sid, ag)}


@_try_tool
def state_delete(key: str) -> Dict[str, Any]:
    sid, ag = _ctx_session()
    ok = delete_state(sid, ag, key)
    return {"ok": ok, "deleted": key}
