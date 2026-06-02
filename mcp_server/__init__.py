"""Dash MCP Server package.

Two transports:

* ``mcp_server.main``        — JSON-RPC 2.0 over stdio (for Claude Code /
  Cursor / Windsurf / Cline). Run as::

      python -m mcp_server

  (delegates to ``main.run_stdio``).

* ``mcp_server.http_server`` — FastAPI subapp at ``/api/mcp/rpc`` for
  ChatGPT / Claude Desktop / hosted integrations. Bearer-auth.

Both transports share:

* ``mcp_server.tools_registry`` — single source of truth for the 8 Dash
  tools exposed via MCP (sql_query, recall, apply_skill, search_brain,
  list_projects, get_project_detail, list_skills, list_dashboards).
* ``mcp_server.auth.verify_token`` — Bearer-token validation against
  ``public.dash_tokens`` (reuses the existing Dash auth surface).

The legacy ``mcp_server.server`` (HTTP-passthrough stdio bridge, kept for
backward compatibility) is still importable but new clients should use
``mcp_server.main`` (in-process tool calls, no HTTP roundtrip).
"""

from __future__ import annotations

__version__ = "0.2.0"

# Re-export the registry + a couple of helpers so callers can do
# ``from mcp_server import call_tool`` without poking at submodules.
from .tools_registry import REGISTRY, list_tools, call_tool  # noqa: E402,F401

# Backward-compat: keep the legacy stdio entrypoint importable as
# ``mcp_server.main_legacy`` so existing ``claude_desktop_config.json``
# pointing at ``mcp_server.server:main`` keeps working.
try:  # pragma: no cover - legacy
    from .server import main as main_legacy  # noqa: F401
except Exception:  # pragma: no cover
    pass


def main() -> None:
    """Default entry point — runs the stdio JSON-RPC loop."""
    from .main import run_stdio

    run_stdio()


__all__ = [
    "REGISTRY",
    "list_tools",
    "call_tool",
    "main",
    "__version__",
]
