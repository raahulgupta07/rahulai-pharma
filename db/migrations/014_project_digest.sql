-- 014_project_digest.sql — per-project daily digest subscription columns.
-- Idempotent: re-runs are safe.

ALTER TABLE public.dash_projects
  ADD COLUMN IF NOT EXISTS digest_enabled BOOLEAN DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS digest_email_to TEXT,
  ADD COLUMN IF NOT EXISTS digest_slack_enabled BOOLEAN DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS digest_time_utc TEXT DEFAULT '08:00',
  ADD COLUMN IF NOT EXISTS last_digest_sent_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS last_digest_error TEXT;

COMMENT ON COLUMN public.dash_projects.digest_enabled IS 'master toggle for daily digest delivery';
COMMENT ON COLUMN public.dash_projects.digest_email_to IS 'comma-separated email recipients';
COMMENT ON COLUMN public.dash_projects.digest_slack_enabled IS 'send digest to SLACK_WEBHOOK_URL';
COMMENT ON COLUMN public.dash_projects.digest_time_utc IS 'HH:MM UTC time-of-day for daily send';
COMMENT ON COLUMN public.dash_projects.last_digest_sent_at IS 'last successful or attempted send timestamp';
COMMENT ON COLUMN public.dash_projects.last_digest_error IS 'last send error (truncated to 500)';

CREATE INDEX IF NOT EXISTS idx_dash_projects_digest_enabled
  ON public.dash_projects(digest_enabled)
  WHERE digest_enabled = TRUE;
