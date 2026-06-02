-- Dash B5 — Dream nightly maintenance schedule
-- Inserts a recurring agent schedule that triggers a dream cycle.
-- The agent_schedules system is the cron-style trigger; the dream
-- enqueues minions of kinds: dedupe_entities, recompile_stale_pages,
-- reembed_stale_chunks, prune_old_evidence.
--
-- Idempotent via ON CONFLICT DO NOTHING on PK 'sch_dream001'.
-- Wrapped in DO block to guard against schema drift on older DBs
-- where agent_target / schedule_kind columns may not exist.

DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema='dash' AND table_name='dash_agent_schedules'
      AND column_name='agent_target'
  ) AND EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema='dash' AND table_name='dash_agent_schedules'
      AND column_name='schedule_kind'
  ) THEN
    INSERT INTO dash.dash_agent_schedules (
      id, project_slug, created_by, created_by_agent,
      name, description, schedule_kind, cron_expr, interval_seconds,
      next_run_at, prompt, agent_target, enabled, max_runs
    )
    VALUES (
      'sch_dream001',
      NULL,
      0,
      'system',
      'Dream nightly maintenance',
      'Enqueues dedupe / re-embed / prune / recompile minions for all projects.',
      'cron',
      '15 3 * * *',
      NULL,
      now() + INTERVAL '1 day',
      'Run the dream maintenance cycle: enqueue dedupe_entities, recompile_stale_pages, reembed_stale_chunks, prune_old_evidence minions for each active project.',
      'leader',
      true,
      NULL
    )
    ON CONFLICT (id) DO NOTHING;
  ELSE
    RAISE NOTICE 'dash_agent_schedules schema mismatch — dream cycle schedule skipped';
  END IF;
END $$;
