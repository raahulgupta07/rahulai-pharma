-- Dash-OS Phase 2E — Agent-callable schedules (recurring/interval/once)

CREATE TABLE IF NOT EXISTS dash.dash_agent_schedules (
  id TEXT PRIMARY KEY,                  -- 'sch_<8hex>'
  project_slug TEXT,
  created_by INTEGER NOT NULL,
  created_by_agent TEXT,                -- NULL = human, else agent name
  name TEXT NOT NULL,
  description TEXT,
  schedule_kind TEXT NOT NULL,          -- 'cron' | 'interval' | 'once'
  cron_expr TEXT,
  interval_seconds INTEGER,
  next_run_at TIMESTAMPTZ,
  prompt TEXT NOT NULL,
  agent_target TEXT NOT NULL DEFAULT 'leader',
  enabled BOOLEAN NOT NULL DEFAULT true,
  max_runs INTEGER,
  run_count INTEGER NOT NULL DEFAULT 0,
  last_run_at TIMESTAMPTZ,
  last_run_result TEXT,
  last_run_error TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS dash.dash_agent_schedule_runs (
  id BIGSERIAL PRIMARY KEY,
  schedule_id TEXT NOT NULL REFERENCES dash.dash_agent_schedules(id) ON DELETE CASCADE,
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ,
  status TEXT,                          -- ok|error|timeout
  response_excerpt TEXT,
  cost_usd NUMERIC,
  error TEXT
);

CREATE INDEX IF NOT EXISTS idx_sched_next ON dash.dash_agent_schedules(enabled, next_run_at);
CREATE INDEX IF NOT EXISTS idx_sched_project ON dash.dash_agent_schedules(project_slug);
CREATE INDEX IF NOT EXISTS idx_sched_runs_recent ON dash.dash_agent_schedule_runs(schedule_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_sched_dedup ON dash.dash_agent_schedules(project_slug, created_by_agent, name);
