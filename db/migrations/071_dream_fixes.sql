-- Dash Dream Reflection — fixes
--
-- 1. Fix failing partial index on precompute_cache (now() not IMMUTABLE)
-- 2. Update sch_dream001 prompt to include reflect_sessions kind
-- 3. Add sch_ab_revert001 (daily 04:00 UTC) + sch_precompute001 (hourly :15)
--    so Docker Compose deployments get the same coverage as K8s Helm CronJobs.
-- 4. Helm CronJobs (dream-reflect-cronjob.yaml) remain authoritative for K8s.
--
-- Idempotent. ON CONFLICT DO UPDATE / DROP INDEX IF EXISTS / DO blocks.

-- 1. Fix the failing precompute_cache TTL index ------------------------------
-- The original 068 migration tried: CREATE INDEX ... WHERE ttl_until > now()
-- but PG rejects non-IMMUTABLE functions in partial index predicates.
-- Replace with a plain index on ttl_until — queries WHERE ttl_until > now()
-- still hit it via range scan, only marginal cost vs partial.

DROP INDEX IF EXISTS dash.idx_precompute_ttl;
CREATE INDEX IF NOT EXISTS idx_precompute_ttl
  ON dash.dash_dream_precompute_cache(ttl_until);

-- 2. Update sch_dream001 prompt to include reflect_sessions kind -------------
-- Existing prompt only lists janitor kinds; without explicit mention the
-- leader agent may not enqueue the new reflection minion.

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM dash.dash_agent_schedules WHERE id='sch_dream001') THEN
    UPDATE dash.dash_agent_schedules
       SET prompt = 'Run the dream maintenance cycle: enqueue dedupe_entities, recompile_stale_pages, reembed_stale_chunks, prune_old_evidence, AND reflect_sessions minions for each active project.',
           updated_at = now()
     WHERE id='sch_dream001';
  END IF;
END $$;

-- 3. Add 2 new schedules for ab-revert + precompute --------------------------

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
    -- A/B revert daily 04:00 UTC (after nightly dream cycle 03:15)
    INSERT INTO dash.dash_agent_schedules (
      id, project_slug, created_by, created_by_agent,
      name, description, schedule_kind, cron_expr, interval_seconds,
      next_run_at, prompt, agent_target, enabled, max_runs
    ) VALUES (
      'sch_ab_revert001',
      NULL,
      0,
      'system',
      'Dream A/B revert daily',
      'Checks promoted anti-patterns / skills / insights against post-promotion judge scores. Reverts items that regressed quality by ≥0.5 points over the 7-day observation window.',
      'cron',
      '0 4 * * *',
      NULL,
      now() + INTERVAL '1 day',
      'Run the A/B revert cycle: enqueue ab_revert_check minion for each active project to test promoted dream findings against judge-score regression.',
      'leader',
      true,
      NULL
    ) ON CONFLICT (id) DO NOTHING;

    -- Precompute execution hourly at :15
    INSERT INTO dash.dash_agent_schedules (
      id, project_slug, created_by, created_by_agent,
      name, description, schedule_kind, cron_expr, interval_seconds,
      next_run_at, prompt, agent_target, enabled, max_runs
    ) VALUES (
      'sch_precompute001',
      NULL,
      0,
      'system',
      'Dream precompute hourly',
      'Executes pending anticipated-query SQL cached during Tier 2 dream-lite cycles. Fills dash_dream_precompute_cache.result_json for Layer 16 chat-time injection.',
      'cron',
      '15 * * * *',
      NULL,
      now() + INTERVAL '15 minutes',
      'Run the precompute execution cycle: enqueue precompute_queries minion for each active project to execute pending anticipated-query SQL and fill the precompute cache.',
      'leader',
      true,
      NULL
    ) ON CONFLICT (id) DO NOTHING;

  ELSE
    RAISE NOTICE 'dash_agent_schedules schema mismatch — dream cron schedules skipped';
  END IF;
END $$;

-- ROLLBACK -------------------------------------------------------------------
-- DELETE FROM dash.dash_agent_schedules WHERE id IN ('sch_ab_revert001','sch_precompute001');
-- UPDATE dash.dash_agent_schedules SET prompt = 'Run the dream maintenance cycle: enqueue dedupe_entities, recompile_stale_pages, reembed_stale_chunks, prune_old_evidence minions for each active project.' WHERE id='sch_dream001';
-- DROP INDEX IF EXISTS dash.idx_precompute_ttl;
-- CREATE INDEX IF NOT EXISTS idx_precompute_ttl ON dash.dash_dream_precompute_cache(ttl_until) WHERE ttl_until > now();
