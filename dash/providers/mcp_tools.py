"""MCP tool factory — converts each discovered MCP tool into an Agno
``@tool``-decorated callable.

Naming convention: ``{provider.id}__{tool_name}``. The double-underscore
separator matches the dialect overlay's documented prefix and keeps tools
unique even when multiple MCP sources expose tools with the same name
(e.g. two filesystem servers, one per project).

The wrapper takes a single JSON string ``arguments_json`` because Agno's
tool decorator works best with primitive parameter types and MCP tool
schemas vary wildly. The wrapper bridges the async ``provider.call_tool``
into the synchronous tool API via :func:`asyncio.run` when no event loop
is running, and degrades gracefully otherwise.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Callable

logger = logging.getLogger(__name__)


def make_tools(provider) -> list[Callable]:
    """Build one Agno tool per discovered MCP tool on ``provider``."""
    try:
        from agno.tools import tool
    except ImportError:
        logger.warning(
            "agno.tools not available — MCP tools cannot register"
        )
        return []

    tools: list[Callable] = []
    for spec in provider._available_tools:
        callable_tool = _make_single_tool(provider, spec, tool)
        if callable_tool is not None:
            tools.append(callable_tool)
    return tools


def _make_single_tool(provider, spec: dict, tool_decorator):
    """Build one ``@tool``-decorated callable for a single MCP tool spec."""
    pid = provider.id
    tool_name = spec["name"]
    full_name = f"{pid}__{tool_name}"
    description = (spec.get("description") or f"MCP tool {tool_name}")[:500]

    @tool_decorator(name=full_name, description=description)
    def call_mcp_tool(arguments_json: str = "{}") -> str:
        """Generic JSON-args invocation. ``arguments_json`` is a JSON string
        encoding the tool's argument object (per its MCP input schema).
        """
        try:
            args = json.loads(arguments_json) if arguments_json else {}
            if not isinstance(args, dict):
                args = {"value": args}
        except Exception:
            args = {"value": arguments_json}

        # Bridge async into sync. If a loop is already running we cannot
        # block on it from this synchronous call site, so degrade clearly.
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # No running loop — safe to use asyncio.run.
            try:
                return asyncio.run(provider.call_tool(tool_name, args))
            except Exception as exc:  # noqa: BLE001
                return (
                    f"MCP CALL ERROR ({tool_name}): {str(exc)[:300]}"
                )
        return (
            f"ERROR: MCP tool {tool_name} can't be called from async "
            "context. Caller must use await."
        )

    return call_mcp_tool
