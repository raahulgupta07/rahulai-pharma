-- Dash-OS Phase 6 — 4-layer eval framework + regression baselines

CREATE TABLE IF NOT EXISTS dash.dash_eval_suites (
  id TEXT PRIMARY KEY,                  -- 'es_<8hex>' or builtin slug
  project_slug TEXT,                    -- NULL = global
  name TEXT NOT NULL,
  description TEXT,
  layer TEXT NOT NULL,                  -- 'smoke' | 'reliability' | 'llm_judge' | 'regression'
  target_agent TEXT,                    -- which agent suite evaluates
  is_builtin BOOLEAN NOT NULL DEFAULT false,
  enabled BOOLEAN NOT NULL DEFAULT true,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(name, project_slug)
);

CREATE TABLE IF NOT EXISTS dash.dash_eval_cases (
  id TEXT PRIMARY KEY,                  -- 'ec_<8hex>'
  suite_id TEXT NOT NULL REFERENCES dash.dash_eval_suites(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  input_prompt TEXT NOT NULL,
  expected_output TEXT,
  expected_tool_calls JSONB,            -- ['run_sql_query', ...] for reliability
  judge_prompt TEXT,                    -- for llm_judge layer
  max_latency_ms INTEGER,
  metadata JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS dash.dash_eval_runs (
  id TEXT PRIMARY KEY,                  -- 'er_<8hex>'
  suite_id TEXT NOT NULL REFERENCES dash.dash_eval_suites(id) ON DELETE CASCADE,
  triggered_by INTEGER,
  status TEXT NOT NULL DEFAULT 'running',
  total_cases INTEGER NOT NULL DEFAULT 0,
  passed INTEGER NOT NULL DEFAULT 0,
  failed INTEGER NOT NULL DEFAULT 0,
  pass_rate NUMERIC,
  avg_latency_ms NUMERIC,
  cost_usd NUMERIC DEFAULT 0,
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ,
  notes TEXT
);

CREATE TABLE IF NOT EXISTS dash.dash_eval_results (
  id BIGSERIAL PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES dash.dash_eval_runs(id) ON DELETE CASCADE,
  case_id TEXT NOT NULL,
  case_name TEXT,
  status TEXT NOT NULL,                 -- 'pass' | 'fail' | 'error'
  score NUMERIC,                        -- 0..1 for llm_judge, 0/1 otherwise
  actual_output TEXT,
  judge_reason TEXT,
  tool_calls_observed JSONB,
  latency_ms INTEGER,
  error TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS dash.dash_eval_baselines (
  id BIGSERIAL PRIMARY KEY,
  suite_id TEXT NOT NULL REFERENCES dash.dash_eval_suites(id) ON DELETE CASCADE,
  pass_rate NUMERIC NOT NULL,
  avg_latency_ms NUMERIC,
  set_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  set_by INTEGER,
  source_run_id TEXT,
  notes TEXT,
  UNIQUE(suite_id, set_at)
);

CREATE TABLE IF NOT EXISTS dash.dash_secret_leaks (
  id BIGSERIAL PRIMARY KEY,
  agent_name TEXT,
  tool_name TEXT,
  project_slug TEXT,
  user_id INTEGER,
  run_id TEXT,
  pattern_matched TEXT NOT NULL,        -- 'api_key' | 'jwt' | 'env_var' | etc
  match_excerpt TEXT,                   -- ±20 chars around the secret (redacted)
  action TEXT NOT NULL,                 -- 'blocked' | 'redacted' | 'logged'
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_eval_runs_recent ON dash.dash_eval_runs(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_eval_runs_suite ON dash.dash_eval_runs(suite_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_eval_results_run ON dash.dash_eval_results(run_id);
CREATE INDEX IF NOT EXISTS idx_eval_baseline_suite ON dash.dash_eval_baselines(suite_id, set_at DESC);
CREATE INDEX IF NOT EXISTS idx_secret_leak_recent ON dash.dash_secret_leaks(created_at DESC);
