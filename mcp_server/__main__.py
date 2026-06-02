"""Entry point: ``python -m mcp_server``.

Routes to the new in-process stdio JSON-RPC loop in ``mcp_server.main``.
The legacy HTTP-passthrough bridge (``mcp_server.server``) is still
importable as ``python -m mcp_server.server`` for backward compat.
"""
from .main import main

if __name__ == "__main__":
    main()
