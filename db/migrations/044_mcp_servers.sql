-- Dash-OS Phase 2C — MCP client server registry + tool bindings + invocation audit

CREATE TABLE IF NOT EXISTS dash.dash_mcp_servers (
  id TEXT PRIMARY KEY,                  -- 'mcp_<8hex>'
  project_slug TEXT,                    -- NULL = global (super-admin only)
  name TEXT NOT NULL,
  transport TEXT NOT NULL,              -- 'stdio' | 'sse' | 'http'
  url TEXT,
  command TEXT,
  args JSONB DEFAULT '[]',
  env JSONB DEFAULT '{}',
  auth_header TEXT,
  enabled BOOLEAN NOT NULL DEFAULT true,
  status TEXT NOT NULL DEFAULT 'unknown',  -- unknown|connected|failed
  discovered_tools JSONB DEFAULT '[]',
  last_health_at TIMESTAMPTZ,
  last_error TEXT,
  created_by INTEGER,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS dash.dash_mcp_tool_bindings (
  id BIGSERIAL PRIMARY KEY,
  server_id TEXT NOT NULL REFERENCES dash.dash_mcp_servers(id) ON DELETE CASCADE,
  agent_name TEXT NOT NULL,             -- 'Analyst' | 'Researcher' | '*' for all
  tool_name TEXT NOT NULL,
  enabled BOOLEAN NOT NULL DEFAULT true,
  UNIQUE(server_id, agent_name, tool_name)
);

CREATE TABLE IF NOT EXISTS dash.dash_mcp_invocations (
  id BIGSERIAL PRIMARY KEY,
  server_id TEXT,
  tool_name TEXT NOT NULL,
  project_slug TEXT,
  user_id INTEGER,
  run_id TEXT,
  args JSONB,
  result JSONB,
  latency_ms INTEGER,
  status TEXT,                          -- ok|error|timeout
  error TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_mcp_servers_project ON dash.dash_mcp_servers(project_slug);
CREATE INDEX IF NOT EXISTS idx_mcp_inv_recent ON dash.dash_mcp_invocations(created_at DESC);
