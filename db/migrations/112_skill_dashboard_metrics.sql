-- 112_skill_dashboard_metrics.sql
-- SkillRefinery reward signal for dashboard skills + per-tenant skill overrides + audit log per dashboard.

-- Per-dashboard-run quality signal (for SkillRefinery reward computation)
CREATE TABLE IF NOT EXISTS public.dash_dashboard_skill_runs (
  id SERIAL PRIMARY KEY,
  project_slug TEXT NOT NULL,
  dashboard_id TEXT,
  skill_id TEXT NOT NULL,
  skill_version INT,
  stage TEXT NOT NULL,                    -- e.g. 'narrator', 'refiner'
  panel_count INT,
  verified_cell_count INT,                -- # cells passing verified-reward
  judge_score INT,                        -- from Phase 4 Vision-QA judge
  latency_ms INT,
  cost_usd DOUBLE PRECISION,
  ran_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_dash_skill_runs_skill ON public.dash_dashboard_skill_runs (skill_id, ran_at DESC);
CREATE INDEX IF NOT EXISTS idx_dash_skill_runs_dash ON public.dash_dashboard_skill_runs (dashboard_id, ran_at DESC);

-- Per-tenant skill override: project-scoped skill beats global
CREATE TABLE IF NOT EXISTS public.dash_skill_overrides (
  id SERIAL PRIMARY KEY,
  project_slug TEXT NOT NULL,
  skill_id TEXT NOT NULL,
  instructions TEXT NOT NULL,
  created_by INT,
  created_at TIMESTAMP DEFAULT NOW(),
  UNIQUE (project_slug, skill_id)
);

-- Audit log per dashboard: which skill versions ran
CREATE TABLE IF NOT EXISTS public.dash_dashboard_audit (
  id SERIAL PRIMARY KEY,
  dashboard_id TEXT NOT NULL,
  skill_versions JSONB NOT NULL,          -- {skill_id: version}
  verified_cell_pct DOUBLE PRECISION,     -- 0-100
  created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_dash_audit_dash ON public.dash_dashboard_audit (dashboard_id, created_at DESC);
