"""Dash-OS Phase 2C — MCP server registry CRUD + bindings + audit."""
from __future__ import annotations

import asyncio
import json as _json
import logging
import secrets
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/mcp", tags=["mcp"])


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


def _user_dep():
    try:
        from app.auth import _get_user
        return _get_user
    except Exception:
        def _noop():
            return {"id": 0, "username": "anonymous", "is_super_admin": False}
        return _noop


_get_user = _user_dep()


class MCPServerIn(BaseModel):
    project_slug: Optional[str] = None
    name: str
    transport: str  # 'stdio' | 'sse' | 'http'
    url: Optional[str] = None
    command: Optional[str] = None
    args: List[str] = []
    env: Dict[str, str] = {}
    auth_header: Optional[str] = None


class BindingsPatch(BaseModel):
    bindings: List[Dict[str, Any]]  # [{agent_name, tool_name, enabled}]


@router.post("/servers")
def create_server(body: MCPServerIn, user=Depends(_get_user)):
    eng = _get_engine()
    if eng is None:
        raise HTTPException(503, "db unavailable")
    if body.transport not in ("stdio", "sse", "http"):
        raise HTTPException(400, "invalid transport")
    sid = "mcp_" + secrets.token_hex(4)
    from sqlalchemy import text
    with eng.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO dash.dash_mcp_servers
                  (id, project_slug, name, transport, url, command, args, env,
                   auth_header, created_by)
                VALUES (:id, :ps, :nm, :tr, :url, :cmd, CAST(:args AS jsonb),
                        CAST(:env AS jsonb), :ah, :cb)
                """
            ),
            {
                "id": sid, "ps": body.project_slug, "nm": body.name,
                "tr": body.transport, "url": body.url, "cmd": body.command,
                "args": _json.dumps(body.args), "env": _json.dumps(body.env),
                "ah": body.auth_header, "cb": user.get("id") if user else None,
            },
        )
    # async discovery (best-effort)
    try:
        from dash.tools.mcp_client import discover_and_register
        asyncio.create_task(discover_and_register(sid))
    except Exception:
        pass
    return {"ok": True, "id": sid}


@router.get("/servers")
def list_servers(
    project_slug: Optional[str] = Query(None),
    user=Depends(_get_user),
):
    eng = _get_engine()
    if eng is None:
        return {"servers": []}
    from sqlalchemy import text
    with eng.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT id, project_slug, name, transport, url, command, enabled,
                       status, last_health_at, last_error,
                       jsonb_array_length(COALESCE(discovered_tools,'[]'::jsonb)) AS tool_count
                FROM dash.dash_mcp_servers
                WHERE (:ps IS NULL OR project_slug = :ps OR project_slug IS NULL)
                ORDER BY created_at DESC
                """
            ),
            {"ps": project_slug},
        ).mappings().all()
    return {"servers": [dict(r) for r in rows]}


@router.get("/servers/{sid}")
def get_server(sid: str, user=Depends(_get_user)):
    eng = _get_engine()
    if eng is None:
        raise HTTPException(503, "db unavailable")
    from sqlalchemy import text
    with eng.connect() as conn:
        row = conn.execute(
            text("SELECT * FROM dash.dash_mcp_servers WHERE id=:id"),
            {"id": sid},
        ).mappings().first()
    if not row:
        raise HTTPException(404, "server_not_found")
    return dict(row)


@router.post("/servers/{sid}/test")
async def test_server(sid: str, user=Depends(_get_user)):
    from dash.tools.mcp_client import discover_and_register
    return await discover_and_register(sid)


@router.post("/servers/{sid}/rediscover")
async def rediscover(sid: str, user=Depends(_get_user)):
    from dash.tools.mcp_client import discover_and_register
    return await discover_and_register(sid)


@router.delete("/servers/{sid}")
def delete_server(sid: str, hard: bool = Query(False), user=Depends(_get_user)):
    eng = _get_engine()
    if eng is None:
        raise HTTPException(503, "db unavailable")
    from sqlalchemy import text
    if hard:
        if not user or not user.get("is_super_admin"):
            raise HTTPException(403, "super_admin_required")
        with eng.begin() as conn:
            conn.execute(text("DELETE FROM dash.dash_mcp_servers WHERE id=:id"), {"id": sid})
    else:
        with eng.begin() as conn:
            conn.execute(
                text("UPDATE dash.dash_mcp_servers SET enabled=false, updated_at=now() WHERE id=:id"),
                {"id": sid},
            )
    return {"ok": True, "deleted": sid, "hard": hard}


@router.get("/servers/{sid}/bindings")
def list_bindings(sid: str, user=Depends(_get_user)):
    eng = _get_engine()
    if eng is None:
        return {"bindings": []}
    from sqlalchemy import text
    with eng.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT agent_name, tool_name, enabled FROM dash.dash_mcp_tool_bindings "
                "WHERE server_id=:sid ORDER BY agent_name, tool_name"
            ),
            {"sid": sid},
        ).mappings().all()
    return {"bindings": [dict(r) for r in rows]}


@router.patch("/servers/{sid}/bindings")
def patch_bindings(sid: str, body: BindingsPatch, user=Depends(_get_user)):
    eng = _get_engine()
    if eng is None:
        raise HTTPException(503, "db unavailable")
    from sqlalchemy import text
    with eng.begin() as conn:
        for b in body.bindings:
            conn.execute(
                text(
                    """
                    INSERT INTO dash.dash_mcp_tool_bindings (server_id, agent_name, tool_name, enabled)
                    VALUES (:sid, :an, :tn, :en)
                    ON CONFLICT (server_id, agent_name, tool_name)
                      DO UPDATE SET enabled = EXCLUDED.enabled
                    """
                ),
                {"sid": sid, "an": b["agent_name"], "tn": b["tool_name"], "en": bool(b.get("enabled", True))},
            )
    return {"ok": True, "patched": len(body.bindings)}


@router.get("/invocations")
def list_invocations(
    server_id: Optional[str] = Query(None),
    days: int = Query(7, ge=1, le=90),
    limit: int = Query(100, ge=1, le=1000),
    user=Depends(_get_user),
):
    eng = _get_engine()
    if eng is None:
        return {"invocations": []}
    from sqlalchemy import text
    with eng.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT server_id, tool_name, project_slug, latency_ms, status,
                       error, created_at
                FROM dash.dash_mcp_invocations
                WHERE created_at > now() - (:d || ' days')::interval
                  AND (:sid IS NULL OR server_id = :sid)
                ORDER BY created_at DESC LIMIT :lim
                """
            ),
            {"d": days, "sid": server_id, "lim": limit},
        ).mappings().all()
    return {"invocations": [dict(r) for r in rows]}
