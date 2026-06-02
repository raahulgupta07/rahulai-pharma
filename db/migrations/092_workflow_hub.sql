-- 092_workflow_hub.sql
-- Cross-agent workflow hub: extend dash_autonomous_workflows with cron scheduling,
-- ownership, cost caps, and last-output cache; add per-run history table.
-- Idempotent. Auto-runner sets search_path = dash, public, ai.
--
-- NOTE: dash_autonomous_workflows is bootstrapped at runtime by dash/templates/storage.py
-- and may live in either `dash` or `public` schema depending on session search_path.
-- Migration auto-detects the existing schema and applies ALTERs there; the run-history
-- table is always created in `dash`.

CREATE SCHEMA IF NOT EXISTS dash;

-- ── Detect schema, then extend dash_autonomous_workflows ────────────────
DO $migration_092$
DECLARE
  v_schema TEXT;
  v_qual TEXT;
BEGIN
  SELECT table_schema INTO v_schema
    FROM information_schema.tables
   WHERE table_name = 'dash_autonomous_workflows'
     AND table_schema IN ('dash', 'public')
   ORDER BY CASE table_schema WHEN 'dash' THEN 1 WHEN 'public' THEN 2 END
   LIMIT 1;

  IF v_schema IS NULL THEN
    -- Table not yet bootstrapped; create it in `dash` so subsequent ALTERs work.
    CREATE TABLE IF NOT EXISTS dash.dash_autonomous_workflows (
      id BIGSERIAL PRIMARY KEY,
      project_slug TEXT NOT NULL,
      template_name TEXT,
      name TEXT NOT NULL,
      description TEXT,
      schedule TEXT,
      query_template TEXT,
      resolved_query TEXT,
      expected_entity TEXT,
      expected_columns JSONB DEFAULT '[]'::jsonb,
      action TEXT,
      status TEXT NOT NULL DEFAULT 'pending',
      last_run_at TIMESTAMPTZ,
      last_error TEXT,
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    v_schema := 'dash';
  END IF;

  v_qual := format('%I.%I', v_schema, 'dash_autonomous_workflows');

  EXECUTE format('ALTER TABLE %s ADD COLUMN IF NOT EXISTS schedule_cron TEXT', v_qual);
  EXECUTE format('ALTER TABLE %s ADD COLUMN IF NOT EXISTS schedule_action TEXT DEFAULT ''post_insight''', v_qual);
  EXECUTE format('ALTER TABLE %s ADD COLUMN IF NOT EXISTS schedule_email TEXT', v_qual);
  EXECUTE format('ALTER TABLE %s ADD COLUMN IF NOT EXISTS schedule_webhook TEXT', v_qual);
  EXECUTE format('ALTER TABLE %s ADD COLUMN IF NOT EXISTS max_cost_usd NUMERIC(8,4) DEFAULT 0.50', v_qual);
  EXECUTE format('ALTER TABLE %s ADD COLUMN IF NOT EXISTS daily_cap_usd NUMERIC(8,4) DEFAULT 5.00', v_qual);
  EXECUTE format('ALTER TABLE %s ADD COLUMN IF NOT EXISTS last_output JSONB', v_qual);
  EXECUTE format('ALTER TABLE %s ADD COLUMN IF NOT EXISTS owner_user_id INTEGER', v_qual);

  EXECUTE format(
    'CREATE INDEX IF NOT EXISTS idx_autonomous_wf_owner ON %s(owner_user_id)',
    v_qual
  );
  EXECUTE format(
    'CREATE INDEX IF NOT EXISTS idx_autonomous_wf_cron ON %s(schedule_cron) WHERE schedule_cron IS NOT NULL',
    v_qual
  );

  -- If table lived in public, create a `dash` view so router queries against
  -- dash.dash_autonomous_workflows continue to work transparently.
  IF v_schema = 'public' THEN
    EXECUTE 'CREATE OR REPLACE VIEW dash.dash_autonomous_workflows AS '
            'SELECT * FROM public.dash_autonomous_workflows';
  END IF;

  RAISE NOTICE 'migration 092: extended %.dash_autonomous_workflows', v_schema;
END
$migration_092$;

-- ── Per-run history (always in dash schema) ─────────────────────────────
CREATE TABLE IF NOT EXISTS dash.dash_workflow_run_history (
  run_id TEXT PRIMARY KEY,
  workflow_id BIGINT NOT NULL,
  project_slug TEXT NOT NULL,
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ,
  duration_ms INTEGER,
  status TEXT NOT NULL,           -- running | done | fail | timeout
  steps_completed INTEGER DEFAULT 0,
  steps_total INTEGER DEFAULT 0,
  cost_usd NUMERIC(8,4) DEFAULT 0,
  output JSONB,
  error TEXT,
  triggered_by TEXT DEFAULT 'cron'  -- cron | manual | event
);

-- Add FK only if the actual workflows table is in dash schema; else skip FK
-- (cross-schema FK to public.dash_autonomous_workflows via view isn't valid).
DO $fk_092$
DECLARE
  v_target_schema TEXT;
  v_fk_exists BOOLEAN;
BEGIN
  SELECT table_schema INTO v_target_schema
    FROM information_schema.tables
   WHERE table_name = 'dash_autonomous_workflows'
     AND table_schema = 'dash'
     AND table_type = 'BASE TABLE'
   LIMIT 1;

  SELECT EXISTS (
    SELECT 1 FROM information_schema.table_constraints
     WHERE table_schema = 'dash'
       AND table_name = 'dash_workflow_run_history'
       AND constraint_name = 'dash_workflow_run_history_workflow_id_fkey'
  ) INTO v_fk_exists;

  IF v_target_schema = 'dash' AND NOT v_fk_exists THEN
    BEGIN
      ALTER TABLE dash.dash_workflow_run_history
        ADD CONSTRAINT dash_workflow_run_history_workflow_id_fkey
        FOREIGN KEY (workflow_id)
        REFERENCES dash.dash_autonomous_workflows(id)
        ON DELETE CASCADE;
    EXCEPTION WHEN others THEN
      RAISE NOTICE 'migration 092: FK skipped: %', SQLERRM;
    END;
  END IF;
END
$fk_092$;

CREATE INDEX IF NOT EXISTS idx_run_hist_wf
  ON dash.dash_workflow_run_history(workflow_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_run_hist_slug
  ON dash.dash_workflow_run_history(project_slug);
CREATE INDEX IF NOT EXISTS idx_run_hist_started
  ON dash.dash_workflow_run_history(started_at DESC);
