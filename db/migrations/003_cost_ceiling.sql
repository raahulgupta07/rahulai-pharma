-- Per-project daily cost cap for self-learning
ALTER TABLE public.dash_projects
  ADD COLUMN IF NOT EXISTS daily_cost_cap_usd NUMERIC(10,4) DEFAULT 1.00,
  ADD COLUMN IF NOT EXISTS cost_paused_until TIMESTAMPTZ;

COMMENT ON COLUMN public.dash_projects.daily_cost_cap_usd
  IS 'Max USD per day for self-learning. 0 = unlimited. Default $1.';
COMMENT ON COLUMN public.dash_projects.cost_paused_until
  IS 'When set, scheduler skips this project until past this timestamp.';

-- Index for fast cost rollup
CREATE INDEX IF NOT EXISTS idx_self_learning_runs_cost_today
  ON public.dash_self_learning_runs(project_slug, started_at);
