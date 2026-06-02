-- Dash-OS Phase 3 — Declarative DAG workflow engine

CREATE TABLE IF NOT EXISTS dash.dash_workflow_defs (
  id TEXT PRIMARY KEY,                  -- 'wf_<8hex>' or builtin slug
  project_slug TEXT,                    -- NULL = global
  name TEXT NOT NULL,
  description TEXT,
  category TEXT,                        -- 'data' | 'research' | 'content' | 'ops' | 'support'
  spec JSONB NOT NULL,                  -- {steps: [{id, kind, agent, prompt, depends_on, parallel_group, loop_until, max_iter, on_error, hitl_gate}]}
  is_builtin BOOLEAN NOT NULL DEFAULT false,
  enabled BOOLEAN NOT NULL DEFAULT true,
  trigger_kind TEXT NOT NULL DEFAULT 'manual',  -- 'manual' | 'cron' | 'event'
  cron_expr TEXT,
  created_by INTEGER,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS dash.dash_workflow_runs (
  id TEXT PRIMARY KEY,                  -- 'wfr_<8hex>'
  def_id TEXT NOT NULL REFERENCES dash.dash_workflow_defs(id) ON DELETE CASCADE,
  project_slug TEXT,
  triggered_by INTEGER,
  trigger_kind TEXT NOT NULL DEFAULT 'manual',
  status TEXT NOT NULL DEFAULT 'pending',  -- pending|running|done|failed|cancelled|hitl_wait
  input_payload JSONB,
  output_payload JSONB,
  error TEXT,
  cost_usd NUMERIC DEFAULT 0,
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS dash.dash_workflow_run_steps (
  id BIGSERIAL PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES dash.dash_workflow_runs(id) ON DELETE CASCADE,
  step_id TEXT NOT NULL,
  step_kind TEXT NOT NULL,              -- 'agent' | 'tool' | 'router' | 'parallel' | 'loop' | 'hitl'
  iter INTEGER DEFAULT 0,
  status TEXT NOT NULL,                 -- running|done|failed|skipped|hitl_wait
  input JSONB,
  output JSONB,
  error TEXT,
  latency_ms INTEGER,
  cost_usd NUMERIC DEFAULT 0,
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_wfdef_project ON dash.dash_workflow_defs(project_slug, enabled);
CREATE INDEX IF NOT EXISTS idx_wfdef_category ON dash.dash_workflow_defs(category);
CREATE INDEX IF NOT EXISTS idx_wfrun_recent ON dash.dash_workflow_runs(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_wfrun_def ON dash.dash_workflow_runs(def_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_wfrun_status ON dash.dash_workflow_runs(status);
CREATE INDEX IF NOT EXISTS idx_wfrunstep_run ON dash.dash_workflow_run_steps(run_id, started_at);
