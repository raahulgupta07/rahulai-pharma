"""Dash MCP stdio entry point.

Implements the Model Context Protocol (https://modelcontextprotocol.io)
over JSON-RPC 2.0 on stdin/stdout. Spoken by Claude Code, Cursor,
Windsurf, Cline, etc.

Methods supported
-----------------

* ``initialize`` — handshake. Returns ``serverInfo`` + ``capabilities``.
* ``initialized`` (notification) — client→server ack. No-op.
* ``ping`` — round-trip health.
* ``tools/list`` — returns the 8 Dash tools from ``tools_registry``.
* ``tools/call`` — invokes a tool, returns ``content`` with stringified
  JSON inside an ``mcp.text`` block.
* ``resources/list`` — exposes the project catalog as MCP resources
  with ``dash://project/<slug>`` URIs.
* ``resources/read`` — returns project detail (JSON) for a project URI.
* ``prompts/list`` — returns starter prompts (analysis templates).

Authentication
--------------

The client process inherits ``DASH_MCP_USER_TOKEN`` from its env. We
resolve it once at startup. If the token is invalid we still respond to
``initialize`` (so the client sees a clean error) but every
``tools/call`` returns the standard MCP error envelope.

Implementation notes
--------------------

* JSON-RPC 2.0 over LSP-style ``Content-Length`` framing AND
  newline-delimited JSON — the spec allows either; we sniff the first
  byte (`{` = ndjson, `C` = framed).
* No external deps: we implement JSON-RPC by hand (~250 LOC) to keep
  the stdio entry trivially deployable.
* Logs go to **stderr** only (stdout is reserved for protocol frames).
"""

from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any

from .auth import verify_token
from .tools_registry import call_tool, list_tools

# ---------------------------------------------------------------------------
# Logging: stderr only.
# ---------------------------------------------------------------------------

logging.basicConfig(
    stream=sys.stderr,
    level=os.getenv("DASH_MCP_LOG_LEVEL", "INFO"),
    format="%(asctime)s mcp.stdio %(levelname)s %(message)s",
)
log = logging.getLogger("mcp.stdio")

PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "dash-mcp"
SERVER_VERSION = "0.2.0"

# JSON-RPC error codes
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
UNAUTHORIZED = -32000


# ---------------------------------------------------------------------------
# Framing helpers (read/write JSON-RPC messages on stdio)
# ---------------------------------------------------------------------------

def _read_message() -> dict | None:
    """Read one JSON-RPC message from stdin.

    Supports two framings:

    * ``Content-Length: N\\r\\n\\r\\n<json>`` (LSP / official MCP)
    * Newline-delimited JSON (``<json>\\n``) — convenient for piped
      shell testing.
    """
    line = sys.stdin.readline()
    if not line:
        return None  # EOF

    line = line.strip()
    if not line:
        return _read_message()

    if line.startswith("{"):
        try:
            return json.loads(line)
        except json.JSONDecodeError as e:
            log.warning("ndjson parse error: %s", e)
            return {"_parse_error": str(e)}

    if line.lower().startswith("content-length:"):
        try:
            length = int(line.split(":", 1)[1].strip())
        except ValueError:
            return {"_parse_error": "bad content-length"}
        # consume blank line(s)
        while True:
            sep = sys.stdin.readline()
            if not sep or sep.strip() == "":
                break
        body = sys.stdin.read(length)
        try:
            return json.loads(body)
        except json.JSONDecodeError as e:
            return {"_parse_error": str(e)}

    log.debug("ignoring stray line: %r", line[:80])
    return _read_message()


def _write_message(msg: dict) -> None:
    payload = json.dumps(msg, separators=(",", ":"), ensure_ascii=False)
    # Emit in framed form for spec-correct clients; ndjson clients also
    # accept this because they look for ``{...}\n``.
    body = payload.encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii"))
    sys.stdout.buffer.write(body)
    sys.stdout.buffer.write(b"\n")
    sys.stdout.buffer.flush()


def _ok(req_id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _err(req_id: Any, code: int, message: str, data: Any = None) -> dict:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": err}


# ---------------------------------------------------------------------------
# Method handlers
# ---------------------------------------------------------------------------

def _handle_initialize(params: dict, user: dict | None) -> dict:
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        "capabilities": {
            "tools": {"listChanged": False},
            "resources": {"listChanged": False, "subscribe": False},
            "prompts": {"listChanged": False},
            "logging": {},
        },
        "instructions": (
            "Dash MCP server. Use `dash_list_projects` to discover "
            "projects, then `dash_get_project_detail` for metadata. "
            "Run `dash_sql_query` for read-only SELECT, `dash_recall` "
            "for semantic search across the project brain, "
            "`dash_search_brain` for Company Brain lookups, "
            "`dash_apply_skill` to execute a proven skill (requires "
            "editor role)."
        ),
        "_authenticated": bool(user),
    }


def _handle_tools_list(params: dict, user: dict | None) -> dict:
    return {"tools": list_tools()}


def _handle_tools_call(params: dict, user: dict | None) -> dict:
    if user is None:
        raise _AuthError("missing or invalid DASH_MCP_USER_TOKEN")

    name = params.get("name")
    if not name:
        raise _InvalidParams("missing tool name")
    arguments = params.get("arguments") or {}

    try:
        result = call_tool(name, user, arguments)
    except KeyError:
        raise _MethodNotFound(f"unknown tool: {name}")

    # MCP wants a `content` list. We stringify the JSON result.
    text = json.dumps(result, default=str, ensure_ascii=False)
    is_error = bool(result.get("error") or result.get("ok") is False)
    return {
        "content": [{"type": "text", "text": text}],
        "isError": is_error,
        "structuredContent": result,
    }


def _handle_resources_list(params: dict, user: dict | None) -> dict:
    if user is None:
        return {"resources": []}
    projects = call_tool("dash_list_projects", user, {})
    if not projects.get("ok"):
        return {"resources": []}
    return {
        "resources": [
            {
                "uri": f"dash://project/{p['slug']}",
                "name": p["name"],
                "mimeType": "application/json",
                "description": f"Dash project '{p['name']}' (agent: {p.get('agent_name','?')})",
            }
            for p in projects.get("projects", [])
        ],
    }


def _handle_resources_read(params: dict, user: dict | None) -> dict:
    if user is None:
        raise _AuthError("missing or invalid DASH_MCP_USER_TOKEN")
    uri = params.get("uri", "")
    if not uri.startswith("dash://project/"):
        raise _InvalidParams("only dash://project/<slug> URIs supported")
    slug = uri[len("dash://project/"):]
    detail = call_tool("dash_get_project_detail", user, {"slug": slug})
    return {
        "contents": [
            {
                "uri": uri,
                "mimeType": "application/json",
                "text": json.dumps(detail, default=str, ensure_ascii=False),
            }
        ],
    }


def _handle_prompts_list(params: dict, user: dict | None) -> dict:
    return {
        "prompts": [
            {
                "name": "dash_explore_project",
                "description": "Start an analysis of a Dash project.",
                "arguments": [{"name": "project_slug", "required": True}],
            },
            {
                "name": "dash_quick_kpi",
                "description": "Pull a KPI from a project with the read-only SQL tool.",
                "arguments": [
                    {"name": "project_slug", "required": True},
                    {"name": "kpi", "required": True},
                ],
            },
        ]
    }


def _handle_ping(params: dict, user: dict | None) -> dict:
    return {}


# Internal exception types for cleaner error mapping
class _AuthError(Exception):
    pass


class _InvalidParams(Exception):
    pass


class _MethodNotFound(Exception):
    pass


_METHODS = {
    "initialize": _handle_initialize,
    "ping": _handle_ping,
    "tools/list": _handle_tools_list,
    "tools/call": _handle_tools_call,
    "resources/list": _handle_resources_list,
    "resources/read": _handle_resources_read,
    "prompts/list": _handle_prompts_list,
}


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def _dispatch(msg: dict, user: dict | None) -> dict | None:
    """Return the response dict, or None for notifications."""
    if msg.get("_parse_error"):
        return _err(None, PARSE_ERROR, msg["_parse_error"])

    if msg.get("jsonrpc") != "2.0":
        return _err(msg.get("id"), INVALID_REQUEST, "jsonrpc must be '2.0'")

    method = msg.get("method")
    req_id = msg.get("id")  # notifications have no id

    if not method:
        return _err(req_id, INVALID_REQUEST, "missing method")

    # Notifications: client→server only, no response.
    if req_id is None and method == "initialized":
        log.info("mcp client initialized")
        return None
    if req_id is None and method.startswith("notifications/"):
        return None

    handler = _METHODS.get(method)
    if handler is None:
        return _err(req_id, METHOD_NOT_FOUND, f"unknown method: {method}")

    try:
        result = handler(msg.get("params") or {}, user)
        return _ok(req_id, result)
    except _AuthError as e:
        return _err(req_id, UNAUTHORIZED, str(e))
    except _InvalidParams as e:
        return _err(req_id, INVALID_PARAMS, str(e))
    except _MethodNotFound as e:
        return _err(req_id, METHOD_NOT_FOUND, str(e))
    except Exception as e:
        log.exception("handler crashed")
        return _err(req_id, INTERNAL_ERROR, str(e))


def run_stdio() -> None:
    """Read JSON-RPC requests from stdin in a loop; reply on stdout."""
    token = os.getenv("DASH_MCP_USER_TOKEN", "").strip()
    user = verify_token(token) if token else None
    if token and not user:
        log.warning("DASH_MCP_USER_TOKEN provided but did not resolve to a Dash user")
    elif user:
        log.info("mcp stdio authenticated as %s (id=%s, super=%s)",
                 user.get("username"), user.get("user_id"), user.get("is_super"))
    else:
        log.warning("no DASH_MCP_USER_TOKEN set; tools/call will return unauthorized")

    log.info("dash-mcp stdio v%s ready (protocol %s, %d tools)",
             SERVER_VERSION, PROTOCOL_VERSION, len(list_tools()))

    while True:
        msg = _read_message()
        if msg is None:
            log.info("eof on stdin, exiting")
            return
        response = _dispatch(msg, user)
        if response is not None:
            _write_message(response)


def main() -> None:  # pragma: no cover - thin wrapper
    try:
        run_stdio()
    except KeyboardInterrupt:
        log.info("interrupt; exiting")
        sys.exit(0)


if __name__ == "__main__":
    main()
