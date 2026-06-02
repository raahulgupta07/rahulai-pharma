# MCP Provider

The MCP provider wraps any [Model Context Protocol](https://modelcontextprotocol.io)
server as a Dash data source. All tools advertised by the MCP server are
surfaced to the Researcher and/or Analyst agents through the same provider
abstraction used for SQL, OneDrive, SharePoint, and Google Drive sources.

## Use cases

- **Brave search MCP server** -> web search tool for the Researcher.
- **GitHub MCP server** -> repo file access, issue / PR lookup.
- **Filesystem MCP server** -> read-only access to a project folder.
- **Custom enterprise MCP server** -> company-specific tools (ticketing,
  CRM lookups, internal search).

## Setup

1. **Settings -> SOURCES -> MCP**.
2. Choose a transport:
   - **stdio**: provide the command + args. Example:
     `npx -y @modelcontextprotocol/server-filesystem /data`.
   - **http**: provide the SSE URL of a remote MCP server.
3. Save. Dash auto-discovers the available tools on first load.

The provider self-registers under provider class `mcp`, so any
`dash_data_sources` row with `source_type='mcp'` is picked up by the
existing registry without code changes.

## Config schema

Stored as JSONB in `dash_data_sources.config`:

```json
{
  "transport": "stdio",
  "command": ["npx", "-y", "@modelcontextprotocol/server-brave-search"],
  "args": [],
  "env": {"BRAVE_API_KEY": "..."},
  "url": "https://mcp.example.com/sse",
  "headers": {"Authorization": "Bearer ..."},
  "timeout_s": 30,
  "agent_scope": "shared",
  "mode": "live"
}
```

`command` may be a string or a list. When it is a list, the first element
is the executable and the rest are appended to `args`. Only `command` (for
stdio) or `url` (for http) is required; everything else is optional.

`agent_scope` accepts `project`, `analyst_only`, `researcher_only`, or
`shared` -- same semantics as every other Dash provider.

## Tool naming

Each MCP tool is exposed as an Agno tool named:

```
{provider.id}__{mcp_tool_name}
```

For an MCP source with `source_id=42` exposing `search` and `fetch`, the
agent sees `mcp_42__search` and `mcp_42__fetch`. The double-underscore
prefix prevents collisions across multiple MCP sources on the same project.

Tools accept a single `arguments_json` string (a JSON-encoded object
matching the MCP tool's input schema). The wrapper parses it, calls the
async MCP client, and returns the concatenated text content (capped at
8KB).

## Examples

### Brave search (stdio)

```json
{
  "transport": "stdio",
  "command": ["npx", "-y", "@modelcontextprotocol/server-brave-search"],
  "env": {"BRAVE_API_KEY": "your-key"},
  "agent_scope": "researcher_only"
}
```

### Filesystem (stdio)

```json
{
  "transport": "stdio",
  "command": ["npx", "-y", "@modelcontextprotocol/server-filesystem", "/data"],
  "agent_scope": "shared"
}
```

### Remote MCP server (HTTP/SSE)

```json
{
  "transport": "http",
  "url": "https://mcp.internal.example.com/sse",
  "headers": {"Authorization": "Bearer ${MCP_TOKEN}"},
  "agent_scope": "shared"
}
```

## Failure modes

- The `mcp` Python package is imported lazily; if it is missing, `setup()`
  marks the provider `degraded` with a clear error rather than crashing
  the registry.
- Subprocess launch failures (bad command, missing binary) are caught and
  surfaced via `provider.last_error`.
- A degraded provider's tools all short-circuit with a deterministic error
  string -- agents can keep working with other sources.
- Tool output is truncated at 8KB to bound prompt cost.

## Dependencies

```text
mcp>=0.1.0
```

Already pinned in `requirements.txt` (currently `mcp==1.27.0`).
