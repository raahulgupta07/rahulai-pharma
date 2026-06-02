-- 125_workflow_runner_columns.sql
-- Add columns to dash.dash_workflow_run_history so the workflow_runner daemon
-- can claim queued runs, track source, attach the auto-built dashboard, and
-- record ownership.
--
-- Idempotent. Auto-runner sets search_path = dash, public, ai.
--
-- New columns:
--   source           — 'manual' | 'cron' | 'scheduler' | 'api' | 'auto'    (default 'manual')
--   enqueued_at      — when the run was queued (defaults to started_at)
--   dashboard_id     — id of the dashboard built from this run (FK loose: text)
--   owner_user_id    — user that owns the workflow at enqueue time
--   status           — extended values: 'queued' added (existing values kept)

ALTER TABLE dash.dash_workflow_run_history
  ADD COLUMN IF NOT EXISTS source         TEXT,
  ADD COLUMN IF NOT EXISTS enqueued_at    TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS dashboard_id   TEXT,
  ADD COLUMN IF NOT EXISTS owner_user_id  INTEGER;

-- backfill: rows w/o source inherit triggered_by; rows w/o enqueued_at inherit started_at
UPDATE dash.dash_workflow_run_history
   SET source = COALESCE(source, triggered_by, 'manual'),
       enqueued_at = COALESCE(enqueued_at, started_at)
 WHERE source IS NULL OR enqueued_at IS NULL;

-- partial index for the daemon claim path
CREATE INDEX IF NOT EXISTS idx_run_hist_queued
  ON dash.dash_workflow_run_history(enqueued_at)
  WHERE status = 'queued';

CREATE INDEX IF NOT EXISTS idx_run_hist_dashboard
  ON dash.dash_workflow_run_history(dashboard_id)
  WHERE dashboard_id IS NOT NULL;
