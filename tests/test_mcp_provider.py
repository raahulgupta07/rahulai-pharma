"""Unit tests for :mod:`dash.providers.mcp` — the Model Context Protocol
provider wrapper.

All tests are pure pytest with mocks. No subprocesses are launched and the
``mcp`` package is not required (its absence is one of the tested paths).
"""
from __future__ import annotations

import asyncio
import importlib
import sys
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# agno.tools shim — same trick test_providers.py uses, in case this file
# runs in isolation before that one has bootstrapped the stub.
# ---------------------------------------------------------------------------

def _ensure_agno_stub() -> None:
    if "agno.tools" in sys.modules:
        return
    try:
        importlib.import_module("agno.tools")
        return
    except Exception:
        pass
    agno_pkg = ModuleType("agno")
    agno_pkg.__path__ = []
    tools_mod = ModuleType("agno.tools")

    def _tool(*args: Any, **kwargs: Any):
        def _decorate(fn):
            fn.tool_name = kwargs.get("name", getattr(fn, "__name__", ""))
            fn.tool_description = kwargs.get("description", "")
            return fn

        if args and callable(args[0]) and not kwargs:
            return _decorate(args[0])
        return _decorate

    tools_mod.tool = _tool
    sys.modules["agno"] = agno_pkg
    sys.modules["agno.tools"] = tools_mod


_ensure_agno_stub()


from dash.providers import mcp as mcp_module  # noqa: E402
from dash.providers.mcp import MCPProvider  # noqa: E402
from dash.providers.registry import (  # noqa: E402
    _PROVIDER_CLASSES,
    register_provider_class,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.run(coro)


def _make_provider(**config_overrides) -> MCPProvider:
    cfg = {"transport": "stdio", "command": ["node", "x.js"]}
    cfg.update(config_overrides)
    return MCPProvider(
        project_slug="proj1", source_id=42, name="brave-search", config=cfg,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_mcp_provider_instantiates_w_stdio_config():
    p = _make_provider(transport="stdio", command=["node", "srv.js"])
    assert p.id == "mcp_42"
    assert p.name == "brave-search"
    assert p.dialect == "mcp"
    assert p.config["transport"] == "stdio"
    assert p.agent_scope == "shared"


def test_mcp_provider_instantiates_w_http_config():
    p = _make_provider(transport="http", url="https://mcp.example.com/sse")
    assert p.config["transport"] == "http"
    assert p.config["url"] == "https://mcp.example.com/sse"


def test_emit_tools_returns_empty_when_no_tools_discovered():
    p = _make_provider()
    p._available_tools = []
    assert p.emit_tools() == []


def test_setup_marks_degraded_on_missing_command():
    p = MCPProvider(
        project_slug="proj1", source_id=7, name="bad",
        config={"transport": "stdio"},  # no command
    )
    asyncio.run(p.setup())
    assert p.degraded is True
    assert p.last_error  # any error captured (missing command or mcp pkg)


def test_setup_marks_degraded_when_mcp_not_installed():
    p = _make_provider()
    # Force ImportError inside _setup_stdio by hiding the mcp package.
    with patch.dict(sys.modules, {"mcp": None, "mcp.client.stdio": None}):
        asyncio.run(p.setup())
    assert p.degraded is True
    assert p.last_error  # some error string captured


def test_call_tool_when_degraded_returns_error_string():
    p = _make_provider()
    p.degraded = True
    p.last_error = "boom"
    out = asyncio.run(p.call_tool("search", {"q": "hi"}))
    assert "ERROR" in out
    assert "boom" in out


def test_introspect_includes_tool_count():
    p = _make_provider()
    p._available_tools = [
        {"name": "search", "description": "", "input_schema": {}},
        {"name": "fetch", "description": "", "input_schema": {}},
    ]
    info = p.introspect()
    assert info["dialect"] == "mcp"
    assert info["transport"] == "stdio"
    assert info["tool_count"] == 2
    assert set(info["tools"]) == {"search", "fetch"}


def test_dialect_overlay_lists_tools():
    p = _make_provider()
    p._available_tools = [
        {"name": "search", "description": "", "input_schema": {}},
        {"name": "fetch", "description": "", "input_schema": {}},
    ]
    overlay = p.dialect_overlay()
    assert "MCP" in overlay
    assert "search" in overlay
    assert "fetch" in overlay
    assert p.id in overlay


def test_dialect_overlay_degraded_branch():
    p = _make_provider()
    p.degraded = True
    p.last_error = "subprocess died"
    overlay = p.dialect_overlay()
    assert "DEGRADED" in overlay
    assert "subprocess died" in overlay


def test_dialect_overlay_no_tools_branch():
    p = _make_provider()
    p._available_tools = []
    overlay = p.dialect_overlay()
    assert "no tools discovered" in overlay


def test_register_provider_class_registers_mcp():
    # Importing the module triggers registration at import time.
    assert "mcp" in _PROVIDER_CLASSES
    assert _PROVIDER_CLASSES["mcp"] is MCPProvider


def test_call_tool_happy_path_returns_concatenated_text():
    p = _make_provider()
    fake_content = [
        SimpleNamespace(text="hello"),
        SimpleNamespace(text="world"),
    ]
    fake_result = SimpleNamespace(content=fake_content)
    fake_client = MagicMock()
    fake_client.call_tool = AsyncMock(return_value=fake_result)
    p._client = fake_client
    p.degraded = False
    out = asyncio.run(p.call_tool("search", {"q": "hi"}))
    assert "hello" in out and "world" in out
    fake_client.call_tool.assert_awaited_once_with("search", {"q": "hi"})


def test_call_tool_caps_output_at_8kb():
    p = _make_provider()
    big = "x" * 20000
    fake_result = SimpleNamespace(content=[SimpleNamespace(text=big)])
    p._client = MagicMock()
    p._client.call_tool = AsyncMock(return_value=fake_result)
    out = asyncio.run(p.call_tool("dump", {}))
    assert len(out) <= 8192


def test_teardown_clears_state_even_if_client_errors():
    p = _make_provider()

    class _BadClient:
        async def __aexit__(self, *_):
            raise RuntimeError("nope")

    p._client = _BadClient()
    p._stream_ctx = None
    p._available_tools = [{"name": "x", "description": "", "input_schema": {}}]
    asyncio.run(p.teardown())
    assert p._client is None
    assert p._available_tools == []
