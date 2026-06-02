-- Dash-OS Phase 4 — Skills system: domain-expert plug-ins

CREATE TABLE IF NOT EXISTS dash.dash_skills (
  id TEXT PRIMARY KEY,                  -- 'skl_<8hex>' or builtin slug
  project_slug TEXT,                    -- NULL = global
  name TEXT NOT NULL,
  category TEXT,                        -- 'engineering'|'analytics'|'ops'|'vertical'|'meta'
  description TEXT,
  trigger_keywords JSONB DEFAULT '[]',  -- ['code review','PR review',...]
  instructions TEXT NOT NULL,           -- markdown body of SKILL.md
  tools JSONB DEFAULT '[]',             -- [{name, fn_module, fn_name}]
  is_builtin BOOLEAN NOT NULL DEFAULT false,
  enabled BOOLEAN NOT NULL DEFAULT true,
  created_by INTEGER,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(name, project_slug)
);

CREATE TABLE IF NOT EXISTS dash.dash_skill_bindings (
  id BIGSERIAL PRIMARY KEY,
  skill_id TEXT NOT NULL REFERENCES dash.dash_skills(id) ON DELETE CASCADE,
  agent_name TEXT NOT NULL,             -- 'Analyst' | '*' for all
  enabled BOOLEAN NOT NULL DEFAULT true,
  UNIQUE(skill_id, agent_name)
);

CREATE TABLE IF NOT EXISTS dash.dash_skill_invocations (
  id BIGSERIAL PRIMARY KEY,
  skill_id TEXT NOT NULL,
  agent_name TEXT,
  project_slug TEXT,
  user_id INTEGER,
  run_id TEXT,
  trigger_phrase TEXT,
  loaded_chars INTEGER,
  latency_ms INTEGER,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_skills_project ON dash.dash_skills(project_slug, enabled);
CREATE INDEX IF NOT EXISTS idx_skills_cat ON dash.dash_skills(category);
CREATE INDEX IF NOT EXISTS idx_skinv_recent ON dash.dash_skill_invocations(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_skinv_skill ON dash.dash_skill_invocations(skill_id, created_at DESC);
