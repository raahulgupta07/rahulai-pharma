-- 039_hitl.sql — Human-in-the-Loop pending requests
-- Schema-qualified `dash.` because session search_path doesn't include `dash`.

CREATE TABLE IF NOT EXISTS dash.dash_hitl_pending (
  run_id TEXT PRIMARY KEY,
  project_slug TEXT,
  user_id INTEGER,
  agent_name TEXT NOT NULL,
  action_type TEXT NOT NULL,         -- 'confirmation' | 'user_input' | 'external_execution'
  payload JSONB NOT NULL,            -- {tool, args, sql, schema, prompt, ...}
  status TEXT NOT NULL DEFAULT 'pending',  -- pending|approved|rejected|expired|external_done
  response JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ NOT NULL DEFAULT now() + INTERVAL '5 minutes',
  responded_at TIMESTAMPTZ,
  responded_by INTEGER
);

CREATE INDEX IF NOT EXISTS idx_hitl_status_expires ON dash.dash_hitl_pending(status, expires_at);
CREATE INDEX IF NOT EXISTS idx_hitl_project ON dash.dash_hitl_pending(project_slug, created_at DESC);
