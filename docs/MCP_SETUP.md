# Dash MCP Server — Provider Mode

This guide explains how to expose your Dash project agents (Analyst,
Researcher, Engineer, Data Scientist) as **Model Context Protocol (MCP)**
tools so Claude Desktop, Cursor, and Cline can call them directly.

## What is MCP?

MCP is an open standard from Anthropic that lets LLM clients (Claude
Desktop, Cursor, Cline, Continue, etc.) connect to external tool
servers over stdio or HTTP. Once configured, the client sees Dash's
tools next to its built-in ones — you just ask in chat and the model
invokes them.

> **Note**: This is the *provider mode* — Dash *as* an MCP server.
> Dash also has a *consumer mode* (`dash/providers/mcp.py`) that wraps
> external MCP servers as Dash data sources. They are independent.

## Why use Dash via MCP?

- Ask Claude Desktop questions like "What was Q3 revenue per segment?"
  and have it call `dash_chat` against your `proj_demo_crm` project,
  which routes through Leader → Analyst → SQL → DB → response.
- Run ad-hoc read-only SQL through `dash_query_sql` (30s timeout,
  10K row cap, no DML).
- Look up business definitions from the Company Brain via
  `dash_get_brain` ("what's our IRR target?").
- Discover available projects with `dash_list_projects`.

## Install

The Dash MCP server lives at `mcp_server/` in this repo. The `mcp`
Python SDK is already in `requirements.txt`. From the repo root:

```bash
pip install -r requirements.txt   # installs mcp>=1.27
python -m mcp_server --check      # prints config, verifies install
```

You should see:

```
DASH_API_BASE = http://localhost:8001
DASH_API_TOKEN = <set>
Tools: dash_list_projects, dash_chat, dash_query_sql, dash_get_brain
```

## Get a token

The MCP server authenticates to the Dash API using a Bearer token (or
`dash-key-...` API key). To generate one:

```bash
curl -s -X POST http://localhost:8001/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"YOUR_USER","password":"YOUR_PASS"}' | jq -r .token
```

Copy the `token` field. Treat it like a password.

## Claude Desktop config

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`
(macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "dash": {
      "command": "python",
      "args": ["-m", "mcp_server"],
      "cwd": "/absolute/path/to/City-Dash/dash",
      "env": {
        "DASH_API_BASE": "http://localhost:8001",
        "DASH_API_TOKEN": "<paste_token_here>"
      }
    }
  }
}
```

Then **fully quit and restart Claude Desktop**. You should see the
hammer icon in the chat input — click it and verify the four `dash_*`
tools are listed.

## Cursor config

Cursor uses the same JSON schema. Edit `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "dash": {
      "command": "python",
      "args": ["-m", "mcp_server"],
      "cwd": "/absolute/path/to/City-Dash/dash",
      "env": {
        "DASH_API_BASE": "http://localhost:8001",
        "DASH_API_TOKEN": "<paste_token_here>"
      }
    }
  }
}
```

## Cline (VS Code) config

Open the Cline extension settings → **MCP Servers** → **Configure** and
paste the same `mcpServers` block. Cline uses
`~/Library/Application Support/Code/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json`.

## Tools exposed

| Tool | Args | Purpose |
|---|---|---|
| `dash_list_projects` | — | List your project slugs + names |
| `dash_chat` | `project_slug`, `message` | Full agent team chat (Leader → Analyst/Researcher/Engineer/Data Scientist) |
| `dash_query_sql` | `project_slug`, `sql` | Read-only SQL (SELECT/WITH only) |
| `dash_get_brain` | `project_slug`, `query` | Search Company Brain (glossary, formulas, aliases) |

## Example prompts (after restart)

- *"Use the dash MCP server to forecast Q3 revenue from `proj_demo_crm`."*
- *"List my Dash projects and pick the one about HACCP, then ask it for
  the latest non-conformance counts."*
- *"In `proj_demo_quotation_review`, what is the IRR target? Look it up
  in the brain."*
- *"Run `SELECT region, SUM(revenue) FROM sales_2025 GROUP BY 1` against
  `proj_admin_sales_analysis`."*

## Troubleshooting

- **No hammer icon** — Claude Desktop didn't load the config. Check
  the config file path and JSON syntax (use `jq . < config.json`).
  Look at `~/Library/Logs/Claude/mcp*.log`.
- **`401 unauthorized`** — your token expired or is wrong. Re-login
  and update `DASH_API_TOKEN`.
- **`network error calling /api/projects`** — `dash-api` isn't running
  on `DASH_API_BASE`. Start it (`docker compose up -d`).
- **`mcp` package not installed** — the `cwd` you set doesn't have
  `mcp` available. Either install globally or point `command` at a
  venv python: `"command": "/path/to/venv/bin/python"`.
- **Tool returns truncated text** — outputs are capped at 8KB to keep
  context lean. Ask for narrower queries.

## Security notes

- The MCP server runs locally and only connects to `DASH_API_BASE`.
- Never commit `claude_desktop_config.json` — it contains your token.
- `dash_query_sql` rejects anything that isn't `SELECT`/`WITH` and
  inherits the `/api/connectors/query` enforcement (read-only, 30s
  timeout, 10K rows).
- Token scoping follows your Dash user's role and project shares.
