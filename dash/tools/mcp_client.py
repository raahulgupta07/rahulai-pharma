"""Dash-OS Phase 2C — MCP (Model Context Protocol) client.

Connects to external MCP servers (stdio/sse/http), auto-discovers tools,
wraps each as Agno @tool function.

Behind EXPERIMENTAL_AGI=1: tools registered into agents.
Otherwise: registry-only (servers can be configured but not exposed to agents).
"""
from __future__ import annotations

import asyncio
import json as _json
import logging
import os
import secrets
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


def _enabled() -> bool:
    return os.getenv("EXPERIMENTAL_AGI") == "1"


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


class MCPError(Exception):
    pass


class MCPClient:
    """Minimal JSON-RPC 2.0 client for MCP servers.

    Uses official `mcp` SDK if available, else falls back to manual httpx
    for sse/http transports. stdio requires asyncio.subprocess.
    """

    def __init__(self, server_row: Dict[str, Any]):
        self.row = server_row
        self.transport = server_row["transport"]
        self.url = server_row.get("url")
        self.command = server_row.get("command")
        self.args = server_row.get("args") or []
        self.env = server_row.get("env") or {}
        self.auth_header = server_row.get("auth_header")
        self._proc = None  # stdio subprocess
        self._client = None  # httpx client for sse/http
        self._req_id = 0

    async def connect(self) -> None:
        if self.transport == "stdio":
            cmd_parts = self.command.split() + list(self.args)
            self._proc = await asyncio.create_subprocess_exec(
                *cmd_parts,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, **(self.env or {})},
            )
        elif self.transport in ("sse", "http"):
            try:
                import httpx
                headers = {}
                if self.auth_header:
                    k, _, v = self.auth_header.partition(":")
                    headers[k.strip()] = v.strip()
                self._client = httpx.AsyncClient(timeout=30.0, headers=headers)
            except Exception as e:
                raise MCPError(f"httpx unavailable: {e}")
        else:
            raise MCPError(f"unknown transport: {self.transport}")

    async def _rpc(self, method: str, params: Optional[Dict[str, Any]] = None) -> Any:
        self._req_id += 1
        payload = {"jsonrpc": "2.0", "id": self._req_id, "method": method, "params": params or {}}
        if self.transport == "stdio":
            if not self._proc:
                raise MCPError("not connected")
            line = (_json.dumps(payload) + "\n").encode("utf-8")
            self._proc.stdin.write(line)
            await self._proc.stdin.drain()
            resp_line = await asyncio.wait_for(self._proc.stdout.readline(), timeout=30.0)
            if not resp_line:
                raise MCPError("stdio empty response")
            resp = _json.loads(resp_line.decode("utf-8"))
        else:
            if not self._client:
                raise MCPError("not connected")
            r = await self._client.post(self.url, json=payload)
            r.raise_for_status()
            resp = r.json()
        if resp.get("error"):
            err = resp["error"]
            raise MCPError(f"{err.get('code')}: {err.get('message')}")
        return resp.get("result")

    async def list_tools(self) -> List[Dict[str, Any]]:
        result = await self._rpc("tools/list")
        return result.get("tools", []) if isinstance(result, dict) else (result or [])

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> Any:
        return await self._rpc("tools/call", {"name": name, "arguments": arguments})

    async def close(self) -> None:
        if self._proc:
            try:
                self._proc.terminate()
                await asyncio.wait_for(self._proc.wait(), timeout=5.0)
            except Exception:
                pass
        if self._client:
            try:
                await self._client.aclose()
            except Exception:
                pass


async def discover_and_register(server_id: str) -> Dict[str, Any]:
    eng = _get_engine()
    if eng is None:
        return {"ok": False, "error": "db unavailable"}
    from sqlalchemy import text
    with eng.connect() as conn:
        row = conn.execute(
            text("SELECT * FROM dash.dash_mcp_servers WHERE id=:id"),
            {"id": server_id},
        ).mappings().first()
    if not row:
        return {"ok": False, "error": "server_not_found"}
    client = MCPClient(dict(row))
    try:
        await client.connect()
        tools = await client.list_tools()
        with eng.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE dash.dash_mcp_servers
                       SET status='connected', discovered_tools=CAST(:tools AS jsonb),
                           last_health_at=now(), last_error=NULL, updated_at=now()
                     WHERE id=:id
                    """
                ),
                {"id": server_id, "tools": _json.dumps(tools)},
            )
        return {"ok": True, "tool_count": len(tools), "tools": tools}
    except Exception as e:
        with eng.begin() as conn:
            conn.execute(
                text(
                    "UPDATE dash.dash_mcp_servers SET status='failed', "
                    "last_error=:err, last_health_at=now() WHERE id=:id"
                ),
                {"id": server_id, "err": str(e)[:500]},
            )
        return {"ok": False, "error": str(e)}
    finally:
        await client.close()


def make_agno_tools(server_row: Dict[str, Any], agent_name: str) -> List[Callable]:
    """Build Agno @tool wrappers from discovered tools for given agent.

    Filters by dash_mcp_tool_bindings (only enabled for this agent_name or '*').
    Returns [] when EXPERIMENTAL_AGI != "1".
    """
    if not _enabled():
        return []
    discovered = server_row.get("discovered_tools") or []
    if isinstance(discovered, str):
        try:
            discovered = _json.loads(discovered)
        except Exception:
            discovered = []
    if not discovered:
        return []

    # Binding filter
    enabled_names = _enabled_tool_names(server_row["id"], agent_name)
    if enabled_names is not None:
        discovered = [t for t in discovered if t.get("name") in enabled_names]

    out: List[Callable] = []
    server_id = server_row["id"]
    server_name = (server_row.get("name") or "mcp").replace(" ", "_").lower()

    try:
        from agno.tools import tool
    except Exception:
        tool = None  # type: ignore

    for spec in discovered:
        tname = spec.get("name", "unknown")
        tdesc = spec.get("description", "MCP tool")
        full_name = f"mcp_{server_name}_{tname}"

        def _make(server_id=server_id, tname=tname):
            def _invoke(**kwargs):
                return _sync_invoke(server_id, tname, kwargs)
            _invoke.__name__ = full_name
            _invoke.__doc__ = tdesc
            return _invoke

        fn = _make()
        if tool is not None:
            try:
                fn = tool(fn)
            except Exception:
                pass
        out.append(fn)
    return out


def _enabled_tool_names(server_id: str, agent_name: str) -> Optional[set]:
    eng = _get_engine()
    if eng is None:
        return None
    try:
        from sqlalchemy import text
        with eng.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT tool_name FROM dash.dash_mcp_tool_bindings "
                    "WHERE server_id=:sid AND enabled=true AND agent_name IN (:an, '*')"
                ),
                {"sid": server_id, "an": agent_name},
            ).all()
        if not rows:
            return None  # no bindings configured = allow all
        return {r[0] for r in rows}
    except Exception:
        return None


def _sync_invoke(server_id: str, tool_name: str, arguments: Dict[str, Any]) -> Any:
    """Synchronous invocation helper. Logs to dash_mcp_invocations."""
    import time
    started = time.time()
    eng = _get_engine()
    server_row = None
    if eng is not None:
        try:
            from sqlalchemy import text
            with eng.connect() as conn:
                row = conn.execute(
                    text("SELECT * FROM dash.dash_mcp_servers WHERE id=:id AND enabled=true"),
                    {"id": server_id},
                ).mappings().first()
                server_row = dict(row) if row else None
        except Exception as e:
            return {"ok": False, "error": f"db: {e}"}
    if not server_row:
        return {"ok": False, "error": "server_not_available"}

    async def _run():
        client = MCPClient(server_row)
        try:
            await client.connect()
            return await client.call_tool(tool_name, arguments)
        finally:
            await client.close()

    try:
        result = asyncio.run(_run())
        status = "ok"
        err = None
    except Exception as e:
        result = None
        status = "error"
        err = str(e)

    latency_ms = int((time.time() - started) * 1000)
    # fire-and-forget audit (best-effort, never raises)
    if eng is not None:
        try:
            from sqlalchemy import text
            ctx_proj = ctx_user = ctx_run = None
            try:
                from dash.agentic.hooks import (
                    current_project_slug, current_user_id, current_run_id,
                )
                ctx_proj = current_project_slug.get()
                ctx_user = current_user_id.get()
                ctx_run = current_run_id.get()
            except Exception:
                pass
            with eng.begin() as conn:
                conn.execute(
                    text(
                        """
                        INSERT INTO dash.dash_mcp_invocations
                          (server_id, tool_name, project_slug, user_id, run_id,
                           args, result, latency_ms, status, error)
                        VALUES (:sid, :tn, :ps, :uid, :rid,
                                CAST(:args AS jsonb), CAST(:res AS jsonb),
                                :lat, :st, :err)
                        """
                    ),
                    {
                        "sid": server_id, "tn": tool_name,
                        "ps": ctx_proj, "uid": ctx_user, "rid": ctx_run,
                        "args": _json.dumps(arguments)[:5000],
                        "res": _json.dumps(result, default=str)[:5000] if result else None,
                        "lat": latency_ms, "st": status, "err": err,
                    },
                )
        except Exception:  # pragma: no cover
            pass

    if status == "error":
        return {"ok": False, "error": err}
    return {"ok": True, "result": result}
