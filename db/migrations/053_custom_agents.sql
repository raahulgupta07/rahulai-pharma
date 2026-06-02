-- Dash-OS Phase 11A — Sub-agent factory: persisted custom agent definitions
-- and per-spawn run audit. Behind EXPERIMENTAL_AGI=1.

CREATE TABLE IF NOT EXISTS dash.dash_custom_agents (
  id TEXT PRIMARY KEY,                  -- 'cag_<8hex>'
  project_slug TEXT,                    -- NULL = global
  name TEXT NOT NULL,
  description TEXT,
  purpose TEXT,
  base_agent TEXT NOT NULL,             -- 'Analyst'|'Leader'|'Researcher'|...
  agent_md TEXT NOT NULL,               -- frontmatter + body
  scoped_skills JSONB DEFAULT '[]',
  scoped_tools JSONB DEFAULT '[]',
  persona TEXT,
  extra_instructions TEXT,
  created_by_agent TEXT,
  created_by_user INTEGER,
  source TEXT DEFAULT 'spawned',        -- 'spawned'|'manual'|'builtin'
  usage_count INTEGER DEFAULT 0,
  last_used_at TIMESTAMPTZ,
  success_rate NUMERIC,
  enabled BOOLEAN NOT NULL DEFAULT true,
  is_promoted_global BOOLEAN NOT NULL DEFAULT false,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(project_slug, name)
);

CREATE TABLE IF NOT EXISTS dash.dash_subagent_runs (
  id BIGSERIAL PRIMARY KEY,
  agent_id TEXT REFERENCES dash.dash_custom_agents(id) ON DELETE SET NULL,
  agent_name TEXT,
  parent_run_id TEXT,
  spawned_by_agent TEXT,
  project_slug TEXT,
  scoped_skills_used JSONB,
  scoped_tools_used JSONB,
  input_brief TEXT,
  output TEXT,
  status TEXT,                          -- done|error|timeout|killed|denied_nesting
  latency_ms INTEGER,
  cost_usd NUMERIC,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_cagent_project ON dash.dash_custom_agents(project_slug, enabled);
CREATE INDEX IF NOT EXISTS idx_cagent_usage ON dash.dash_custom_agents(usage_count DESC);
CREATE INDEX IF NOT EXISTS idx_subrun_recent ON dash.dash_subagent_runs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_subrun_agent ON dash.dash_subagent_runs(agent_id, created_at DESC);
