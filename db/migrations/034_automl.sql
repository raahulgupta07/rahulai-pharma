-- 034_automl.sql
-- AutoML experiment tracking + event log for SSE replay
-- Auto-runner sets search_path = dash, public, ai. All objects in dash schema.

CREATE TABLE IF NOT EXISTS dash.dash_automl_experiments (
  id BIGSERIAL PRIMARY KEY,
  project_slug TEXT NOT NULL,
  template_id TEXT NOT NULL,
  user_id INT,
  status TEXT NOT NULL DEFAULT 'queued',         -- queued|running|completed|failed|cancelled
  decision_path TEXT,                             -- llm_only|hybrid|flaml
  config JSONB DEFAULT '{}'::jsonb,
  n_rows INT,
  positive_rate NUMERIC(5,4),
  leaderboard JSONB DEFAULT '[]'::jsonb,
  shap_global JSONB DEFAULT '[]'::jsonb,
  shap_per_row JSONB DEFAULT '[]'::jsonb,
  narrative TEXT,
  recommendations TEXT,
  best_model_id INT,
  time_budget INT DEFAULT 600,
  started_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ,
  error TEXT,
  events JSONB DEFAULT '[]'::jsonb,               -- append-only event log (SSE replay)
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_automl_exp_slug_created
  ON dash.dash_automl_experiments(project_slug, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_automl_exp_active
  ON dash.dash_automl_experiments(status)
  WHERE status IN ('queued','running');

CREATE INDEX IF NOT EXISTS idx_automl_exp_template
  ON dash.dash_automl_experiments(template_id);
