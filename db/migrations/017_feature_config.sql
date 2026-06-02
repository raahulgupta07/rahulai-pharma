-- 017_feature_config.sql — per-project feature toggles for agent creator.
-- Idempotent.

ALTER TABLE public.dash_projects
  ADD COLUMN IF NOT EXISTS feature_config JSONB DEFAULT '{}'::jsonb;

COMMENT ON COLUMN public.dash_projects.feature_config IS
  'Agent creator toggles: tabs (analysis/data/query/chart/sources), tools (sql/charts/ml/dashboards/forecast/anomaly), agents (analyst/engineer/researcher/data_scientist)';

CREATE INDEX IF NOT EXISTS idx_dash_projects_feature_config
  ON public.dash_projects USING GIN (feature_config);
