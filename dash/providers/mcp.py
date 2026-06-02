"""MCP provider — wraps any Model Context Protocol server.

Supports two transports:

- ``stdio``: launches a subprocess (e.g. ``node my-mcp-server.js``,
  ``npx -y @modelcontextprotocol/server-filesystem /path``).
- ``http``: connects to a remote MCP server over HTTP/SSE.

On :meth:`setup`, the provider connects to the server and lists available
tools. :meth:`emit_tools` then converts each MCP tool into an Agno
``@tool``-decorated callable so the Researcher and/or Analyst can invoke
any MCP server's tools through Dash's existing routing layer.

Config (stored in ``dash_data_sources.config`` JSONB):

- ``transport``: ``'stdio'`` | ``'http'``
- ``command``: ``str`` or ``list[str]`` (for stdio)
- ``args``: ``list[str]`` (subprocess args, when ``command`` is a string)
- ``env``: ``dict`` (env vars for subprocess)
- ``url``: ``str`` (for http)
- ``headers``: ``dict`` (http headers, e.g. auth)
- ``timeout_s``: ``float`` (default 30)
- ``agent_scope``: ``'project'`` | ``'analyst_only'`` |
  ``'researcher_only'`` | ``'shared'``
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from dash.providers.base import BaseProvider, _DIALECT_QUOTE
from dash.providers.registry import register_provider_class

logger = logging.getLogger(__name__)


# Register a no-op "mcp" dialect so BaseProvider.__init__ doesn't reject it.
# Quote chars are unused for non-SQL providers but the lookup happens at
# construction time. Same trick OneDriveProvider uses.
_DIALECT_QUOTE.setdefault("mcp", ('"', '"'))


class MCPProvider(BaseProvider):
    """Wraps a Model Context Protocol server as a Dash provider."""

    def __init__(
        self,
        project_slug: str,
        source_id: int,
        name: str,
        config: dict | None = None,
    ) -> None:
        cfg = config or {}
        super().__init__(
            id=f"mcp_{source_id}",
            name=name,
            project_slug=project_slug,
            dialect="mcp",
            mode=cfg.get("mode", "live"),
            agent_scope=cfg.get("agent_scope", "shared"),
        )
        self.source_id = source_id
        self.config = cfg
        self._client: Any = None  # MCP ClientSession
        self._available_tools: list[dict] = []
        self._stream_ctx: Any = None  # context manager for stdio/http stream

    # ---- Lifecycle ------------------------------------------------------

    async def setup(self) -> None:
        """Connect to MCP server + discover tools."""
        try:
            transport = self.config.get("transport", "stdio")
            if transport == "stdio":
                await self._setup_stdio()
            elif transport == "http":
                await self._setup_http()
            else:
                raise ValueError(f"unknown transport: {transport}")
            await self._list_tools()
            self.degraded = False
            self.last_error = None
        except Exception as exc:  # noqa: BLE001
            logger.exception("MCP setup failed for %s: %s", self.name, exc)
            self.degraded = True
            self.last_error = str(exc)[:300]

    async def _setup_stdio(self) -> None:
        """Launch MCP server as subprocess."""
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError as exc:
            raise RuntimeError(f"mcp package not installed: {exc}") from exc

        cmd = self.config.get("command")
        if not cmd:
            raise ValueError("stdio transport requires 'command' in config")

        if isinstance(cmd, list):
            command = cmd[0]
            args = list(cmd[1:]) + list(self.config.get("args", []))
        else:
            command = cmd
            args = list(self.config.get("args", []))
        env = self.config.get("env") or None

        params = StdioServerParameters(command=command, args=args, env=env)
        self._stream_ctx = stdio_client(params)
        read_stream, write_stream = await self._stream_ctx.__aenter__()
        self._client = ClientSession(read_stream, write_stream)
        await self._client.__aenter__()
        await self._client.initialize()

    async def _setup_http(self) -> None:
        """Connect to MCP server over HTTP/SSE."""
        try:
            from mcp import ClientSession
            from mcp.client.sse import sse_client
        except ImportError as exc:
            raise RuntimeError(f"mcp package not installed: {exc}") from exc

        url = self.config.get("url")
        if not url:
            raise ValueError("http transport requires 'url' in config")
        headers = self.config.get("headers") or {}

        self._stream_ctx = sse_client(url, headers=headers)
        read_stream, write_stream = await self._stream_ctx.__aenter__()
        self._client = ClientSession(read_stream, write_stream)
        await self._client.__aenter__()
        await self._client.initialize()

    async def _list_tools(self) -> None:
        if not self._client:
            return
        result = await self._client.list_tools()
        self._available_tools = [
            {
                "name": t.name,
                "description": getattr(t, "description", "") or "",
                "input_schema": getattr(t, "inputSchema", {}) or {},
            }
            for t in result.tools
        ]

    async def teardown(self) -> None:
        try:
            if self._client is not None:
                await self._client.__aexit__(None, None, None)
        except Exception:  # noqa: BLE001
            logger.debug("MCP client teardown error", exc_info=True)
        try:
            if self._stream_ctx is not None:
                await self._stream_ctx.__aexit__(None, None, None)
        except Exception:  # noqa: BLE001
            logger.debug("MCP stream teardown error", exc_info=True)
        self._client = None
        self._stream_ctx = None
        self._available_tools = []

    # ---- BaseProvider surface ------------------------------------------

    def introspect(self) -> dict[str, Any]:
        return {
            "dialect": "mcp",
            "transport": self.config.get("transport"),
            "tools": [t["name"] for t in self._available_tools],
            "tool_count": len(self._available_tools),
        }

    def health_check(self) -> bool:
        return self._client is not None and not self.degraded

    def emit_tools(self) -> list:
        try:
            from dash.providers.mcp_tools import make_tools
            return make_tools(self)
        except ImportError:
            logger.debug("mcp_tools module not present; emitting [] tools")
            return []

    def dialect_overlay(self) -> str:
        if self.degraded:
            return (
                f"## SOURCE {self.name} (MCP) — DEGRADED: "
                f"{self.last_error}\n"
            )
        if not self._available_tools:
            return (
                f"## SOURCE {self.name} (MCP) — no tools discovered\n"
            )
        tools_summary = ", ".join(
            t["name"] for t in self._available_tools[:10]
        )
        return (
            f"## SOURCE {self.name} (MCP · {self.mode})\n"
            f"External tool server. {len(self._available_tools)} tools "
            f"available: {tools_summary}\n"
            f"Each tool prefixed `{self.id}__<tool_name>` — call directly "
            "when relevant.\n"
        )

    # ---- MCP-specific --------------------------------------------------

    async def call_tool(self, tool_name: str, arguments: dict) -> str:
        """Invoke an MCP tool. Returns string result (capped at 8KB)."""
        if self.degraded or self._client is None:
            return (
                f"ERROR: MCP source '{self.name}' unavailable. "
                f"{self.last_error or ''}"
            )
        try:
            result = await self._client.call_tool(tool_name, arguments)
            output_parts: list[str] = []
            for c in getattr(result, "content", []) or []:
                if hasattr(c, "text"):
                    output_parts.append(c.text)
                else:
                    output_parts.append(str(c))
            return "\n".join(output_parts)[:8192]
        except Exception as exc:  # noqa: BLE001
            return f"MCP TOOL ERROR ({tool_name}): {str(exc)[:300]}"


register_provider_class("mcp", MCPProvider)
