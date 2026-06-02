-- Add session linkage + versioning to dashboards
ALTER TABLE public.dash_dashboards_v2 ADD COLUMN IF NOT EXISTS session_id TEXT NULL;
ALTER TABLE public.dash_dashboards_v2 ADD COLUMN IF NOT EXISTS version INT NOT NULL DEFAULT 1;
ALTER TABLE public.dash_dashboards_v2 ADD COLUMN IF NOT EXISTS parent_id TEXT NULL;
ALTER TABLE public.dash_dashboards_v2 ADD COLUMN IF NOT EXISTS label TEXT NULL;
CREATE INDEX IF NOT EXISTS idx_dash_v2_session
  ON public.dash_dashboards_v2 (project_slug, session_id, version DESC)
  WHERE session_id IS NOT NULL;
