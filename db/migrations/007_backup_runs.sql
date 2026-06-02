-- Backup tracking table
CREATE TABLE IF NOT EXISTS public.dash_backup_runs (
  id SERIAL PRIMARY KEY,
  env TEXT NOT NULL,
  types TEXT NOT NULL,
  ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  success BOOLEAN NOT NULL DEFAULT TRUE,
  size_bytes BIGINT,
  s3_key TEXT,
  error TEXT,
  duration_seconds INTEGER
);
CREATE INDEX IF NOT EXISTS idx_backup_runs_ts
  ON public.dash_backup_runs(ts DESC);
COMMENT ON TABLE public.dash_backup_runs
  IS 'Audit log of automated backup runs.';
