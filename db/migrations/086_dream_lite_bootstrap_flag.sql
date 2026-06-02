-- 086_dream_lite_bootstrap_flag.sql
-- Replace confusing `status='skipped_no_episodes'` with `bootstrap_ok` +
-- explicit `is_bootstrap` + human-readable `friendly_status` so the UI
-- doesn't render legitimate first-run hooks as failures.
--
-- Idempotent: re-applying on a populated DB is a no-op.

ALTER TABLE dash.dash_dream_lite_runs
  ADD COLUMN IF NOT EXISTS is_bootstrap boolean DEFAULT false;

ALTER TABLE dash.dash_dream_lite_runs
  ADD COLUMN IF NOT EXISTS friendly_status text;

-- Backfill: tag historical skipped_no_episodes rows as bootstrap runs and
-- rewrite their status so existing UI queries see the friendly label.
UPDATE dash.dash_dream_lite_runs
   SET is_bootstrap = true,
       friendly_status = COALESCE(friendly_status, 'Hook fired · no chat data yet'),
       status = 'bootstrap_ok'
 WHERE status = 'skipped_no_episodes';

-- Also backfill friendly_status for already-bootstrap'd rows that were
-- written by a partial earlier deploy (defensive).
UPDATE dash.dash_dream_lite_runs
   SET friendly_status = 'Hook fired · no chat data yet'
 WHERE status = 'bootstrap_ok'
   AND friendly_status IS NULL;

-- Backfill friendly_status for completed real cycles missing the label.
UPDATE dash.dash_dream_lite_runs
   SET friendly_status = 'Reflection complete · ' ||
                         COALESCE(episodes_consumed, 0)::text ||
                         ' episodes consumed'
 WHERE status = 'done'
   AND friendly_status IS NULL;

CREATE INDEX IF NOT EXISTS idx_dream_lite_runs_bootstrap
  ON dash.dash_dream_lite_runs(project_slug, is_bootstrap)
  WHERE is_bootstrap = true;
