#!/usr/bin/env bash
# Install Dash MCP into common AI clients.
#
# Usage:
#   ./install.sh claude-code        # adds to `claude mcp` registry
#   ./install.sh claude-desktop     # prints config snippet for ~/.config
#   ./install.sh cursor             # prints config snippet for ~/.cursor
#   ./install.sh print              # just dump snippets
#
# Env required:
#   DASH_MCP_USER_TOKEN   (Bearer token, mint via POST /api/admin/mcp/tokens)
#
# Default python target: /app/mcp_server/main.py (matches Docker image
# layout). Override with DASH_MCP_ENTRY=/path/to/python -m mcp_server.

set -euo pipefail
TARGET="${1:-print}"
PY="${DASH_MCP_PYTHON:-python}"
ENTRY="${DASH_MCP_ENTRY:-/app/mcp_server/main.py}"
TOKEN="${DASH_MCP_USER_TOKEN:-PASTE_YOUR_TOKEN_HERE}"

case "$TARGET" in
  claude-code)
    if ! command -v claude >/dev/null 2>&1; then
      echo "claude CLI not found on PATH — install Claude Code first." >&2
      exit 1
    fi
    claude mcp add dash "$PY" "$ENTRY" --env DASH_MCP_USER_TOKEN="$TOKEN"
    echo "✓ Added 'dash' to Claude Code MCP registry"
    ;;
  claude-desktop|cursor|print)
    cat <<EOF
# Add to your client config (claude_desktop_config.json / .cursor/mcp.json):

{
  "mcpServers": {
    "dash": {
      "command": "$PY",
      "args": ["$ENTRY"],
      "env": {
        "DASH_MCP_USER_TOKEN": "$TOKEN"
      }
    }
  }
}
EOF
    ;;
  *)
    echo "unknown target: $TARGET" >&2
    echo "usage: $0 {claude-code|claude-desktop|cursor|print}" >&2
    exit 1
    ;;
esac
