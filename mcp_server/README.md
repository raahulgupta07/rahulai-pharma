# Dash MCP Server

Expose Dash's core capabilities — read-only SQL, unified recall, the
proven-skill library, Company Brain search, project discovery — as
[Model Context Protocol](https://modelcontextprotocol.io/) tools so
external coding agents (Claude Code, Cursor, Windsurf, Cline, Claude
Desktop, ChatGPT custom GPTs, n8n) can call Dash directly.

Two transports:

| Transport | Entry point | Best for |
| --- | --- | --- |
| **stdio** (JSON-RPC 2.0) | `python -m mcp_server` | Local IDEs (Claude Code, Cursor, Windsurf, Cline) |
| **HTTP**  (JSON-RPC 2.0) | `POST /api/mcp/rpc`     | Hosted (Claude Desktop HTTP, ChatGPT, n8n) |

Both share the same registry (`tools_registry.py`) and the same
token-based auth (`auth.py`).

---

## 1. Tools exposed

| Name | Purpose | Role required |
| --- | --- | --- |
| `dash_sql_query`        | Read-only SELECT/WITH against a project schema (RLS + read-only enforced, 500-row cap default) | viewer |
| `dash_recall`           | Unified semantic + keyword recall (KB + Brain + KG + grounded facts) via the recall API shipped 2026-05-17 | viewer |
| `dash_apply_skill`      | Execute a proven skill from `dash.dash_skill_library` (Voyager skill library) | editor |
| `dash_search_brain`     | Search Company Brain (`glossary`/`formula`/`alias`/`pattern`/`org`/`threshold`/`calendar`). Scope `global` / `project` / `personal`. | varies |
| `dash_list_projects`    | All projects the user can see (owner + shared; super-admin sees all) | — |
| `dash_get_project_detail` | Codex-enriched metadata (persona, schema, last training run, top 20 tables) | viewer |
| `dash_list_skills`      | Active skills in the project's Voyager library | viewer |
| `dash_list_dashboards`  | List saved dashboards for a project | viewer |

The full JSON schema for each tool's parameters is returned by
`tools/list` — see `tools_registry.py` for the source of truth.

---

## 2. Setup

### 2a. Mint a token (one-time, per user)

The MCP server has its **own** token table (`public.dash_mcp_tokens`)
so a leaked MCP token does not unlock the web UI. Mint one from the
running Dash API:

```bash
curl -s -X POST https://dash.your-host/api/admin/mcp/tokens \
  -H "Authorization: Bearer $YOUR_DASH_SESSION_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"laptop-claude-code","ttl_days":90}'
```

Response:

```json
{
  "token": "dash-mcp-XaY7zKvPq…",
  "name": "laptop-claude-code",
  "scopes": [],
  "expires_at": 1735689600,
  "warning": "Store this token now — it will not be shown again."
}
```

Save the `token` value as `DASH_MCP_USER_TOKEN`. Regular Dash session
tokens and `dash-key-*` API keys also work, but the dedicated
`dash-mcp-*` token is recommended for least-privilege.

List active tokens:

```bash
curl -s -H "Authorization: Bearer $SESSION" \
  https://dash.your-host/api/admin/mcp/tokens
```

Revoke:

```bash
curl -s -X DELETE -H "Authorization: Bearer $SESSION" \
  https://dash.your-host/api/admin/mcp/tokens/$TOKEN_ID
```

### 2b. stdio transport (local IDE)

The stdio bridge runs the **full Dash codebase** (it imports
`db.session`, `app.recall_api`, etc. for in-process tool calls — no
HTTP roundtrip per tool call), so the easiest path is to invoke it
inside the same Docker image as `dash-api`. The
`compose.yaml` ships an optional `dash-mcp` service for exactly this.

**Easiest install:**

```bash
export DASH_MCP_USER_TOKEN="dash-mcp-…"
bash mcp_server/install.sh claude-code
```

The helper script runs:

```bash
claude mcp add dash python /app/mcp_server/main.py \
  --env DASH_MCP_USER_TOKEN="$DASH_MCP_USER_TOKEN"
```

### 2c. HTTP transport (hosted)

Already mounted at `/api/mcp/*` by `app/main.py`. Two endpoints:

* `GET  /api/mcp/info`  — public capability doc, no auth.
* `POST /api/mcp/rpc`   — JSON-RPC 2.0, requires `Authorization: Bearer dash-mcp-…`.

Smoke-test it:

```bash
curl -s -X POST https://dash.your-host/api/mcp/rpc \
  -H "Authorization: Bearer dash-mcp-…" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

---

## 3. Client config snippets

### Claude Code (`claude mcp add` registry)

```bash
claude mcp add dash python /app/mcp_server/main.py \
  --env DASH_MCP_USER_TOKEN="dash-mcp-XaY7zKvPq…"
```

### Cursor (`~/.cursor/mcp.json`)

```jsonc
{
  "mcpServers": {
    "dash": {
      "command": "python",
      "args": ["/app/mcp_server/main.py"],
      "env": { "DASH_MCP_USER_TOKEN": "dash-mcp-XaY7zKvPq…" }
    }
  }
}
```

### Windsurf

Same shape as Cursor — drop into `~/.codeium/windsurf/mcp_config.json`.

### Claude Desktop (`claude_desktop_config.json`)

For the **stdio** path (recommended if you have Docker / a local
checkout):

```jsonc
{
  "mcpServers": {
    "dash": {
      "command": "docker",
      "args": [
        "exec", "-i", "dash-mcp",
        "python", "/app/mcp_server/main.py"
      ],
      "env": { "DASH_MCP_USER_TOKEN": "dash-mcp-…" }
    }
  }
}
```

For the **HTTP** path (no Docker access):

```jsonc
{
  "mcpServers": {
    "dash": {
      "transport": { "type": "http", "url": "https://dash.your-host/api/mcp/rpc" },
      "headers": { "Authorization": "Bearer dash-mcp-…" }
    }
  }
}
```

### ChatGPT custom GPT / n8n

Point the connector at `https://dash.your-host/api/mcp/rpc` with a
Bearer token. The `/api/mcp/info` endpoint advertises the protocol
version and tool list for discovery.

---

## 4. Examples

### List projects (stdio, ndjson framing)

```bash
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' \
  | DASH_MCP_USER_TOKEN="$T" python -m mcp_server
```

### Run SQL (HTTP)

```bash
curl -s -X POST https://dash.your-host/api/mcp/rpc \
  -H "Authorization: Bearer $T" \
  -H "Content-Type: application/json" \
  -d '{
        "jsonrpc":"2.0","id":2,"method":"tools/call",
        "params":{
          "name":"dash_sql_query",
          "arguments":{
            "project_slug":"proj_demo_retail",
            "sql":"SELECT region, SUM(revenue) FROM dash.sales GROUP BY 1 LIMIT 10"
          }
        }
      }' | jq .result.structuredContent
```

### Recall (HTTP)

```bash
curl -s -X POST https://dash.your-host/api/mcp/rpc \
  -H "Authorization: Bearer $T" \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call",
       "params":{"name":"dash_recall",
                 "arguments":{"project_slug":"proj_demo_retail",
                              "q":"what drove the Q3 revenue drop",
                              "top_k":5}}}' | jq .
```

---

## 5. Security model

* Tokens stored in `public.dash_mcp_tokens` (separate from web
  sessions). Revocable; supports per-token TTL.
* Every tool call goes through `auth.can_access_project()` — a
  passthrough to the existing `app.auth.check_project_permission()`
  RBAC (viewer / editor / admin). `dash_apply_skill` requires
  `editor`; everything else requires `viewer`.
* `dash_sql_query` rejects anything that isn't `SELECT` or `WITH` at
  the parser-head level, hard-caps result size to 5000 rows, and
  routes through `db.session.get_project_readonly_engine()` which sets
  `transaction_read_only=on` via SQLAlchemy `begin` events — the
  database refuses writes even if the client lies about intent.
* All SQL is parameterized; identifiers are schema-qualified to
  `dash.*` / `public.*` so search-path drift can't cross-pollute.
* HTTP transport is rate-limited by the same SlowAPI limiter as the
  rest of Dash (`RATE_LIMIT` env, default 500/min).
* Errors never leak stack traces — handlers return `{"ok": false,
  "error": "<message>"}`.

---

## 6. Integration with the rest of Dash

* `app/main.py` includes both routers if `mcp_server.http_server`
  imports cleanly (wrapped in try/except so the rest of the app starts
  even when MCP deps are missing).
* `compose.yaml` ships an optional `dash-mcp` sidecar that mounts the
  same image as `dash-api` and runs `python -m mcp_server` on stdio —
  this is what Claude Desktop / Cursor connect to over `docker exec
  -i`.
* Tool handlers reuse existing Dash internals (`db.session`,
  `app.brain`, `app.recall_api`, `app.skills_api`, `app.auth`) — no
  duplicate logic. If a referenced module is missing on a particular
  deployment, the handler falls back to a raw SQL path so the MCP
  surface keeps working.

---

## 7. Files

```
mcp_server/
├── __init__.py            # re-exports + version
├── main.py                # stdio JSON-RPC loop (~400 LOC)
├── http_server.py         # FastAPI router + admin token endpoints (~250 LOC)
├── tools_registry.py      # 8 tool handlers + schemas (~430 LOC)
├── auth.py                # token verify + project permission shim (~120 LOC)
├── server.py              # LEGACY (HTTP-passthrough stdio bridge) — kept for back-compat
├── install.sh             # helper for `claude mcp add` + config snippets
└── README.md              # this file
```
