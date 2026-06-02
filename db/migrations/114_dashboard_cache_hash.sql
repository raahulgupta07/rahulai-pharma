ALTER TABLE public.dash_dashboards_v2 ADD COLUMN IF NOT EXISTS signature_hash TEXT NULL;
CREATE INDEX IF NOT EXISTS idx_dash_v2_signature
  ON public.dash_dashboards_v2 (project_slug, signature_hash)
  WHERE signature_hash IS NOT NULL;
