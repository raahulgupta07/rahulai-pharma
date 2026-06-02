CREATE TABLE IF NOT EXISTS public.dash_admin_settings (
  id SERIAL PRIMARY KEY,
  key TEXT NOT NULL,
  value TEXT NOT NULL,
  value_type TEXT NOT NULL DEFAULT 'string',  -- 'bool'|'int'|'float'|'string'|'json'|'cron'|'enum'
  scope TEXT NOT NULL DEFAULT 'global',         -- 'global'|'project'
  project_slug TEXT,
  description TEXT,
  updated_by INTEGER,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT uq_setting_key UNIQUE (key, scope, project_slug)
);
CREATE INDEX IF NOT EXISTS idx_admin_settings_key ON public.dash_admin_settings(key);
CREATE INDEX IF NOT EXISTS idx_admin_settings_scope ON public.dash_admin_settings(scope, project_slug);

COMMENT ON TABLE public.dash_admin_settings
  IS 'Runtime config managed via admin UI. Resolution order: project > global > env > default.';
