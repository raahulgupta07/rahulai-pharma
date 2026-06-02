-- 2026-05-25 Phase 8: Schema-prefix normalization.
-- Code references like `dash.dash_dream_reflection_tree` were not updated
-- when the table moved to `public.dash_dream_reflection_tree` (or never moved).
-- This migration creates compatibility views so old code keeps working
-- while we fix references in app code (Phase 8.2).
--
-- Pattern: if table exists in public but code expects dash., create a view.
-- Views are not materialized — zero storage overhead, transparent passthrough.

CREATE SCHEMA IF NOT EXISTS dash;

DO $$
DECLARE
  _t TEXT;
  _real_schema TEXT;
BEGIN
  -- List of tables where code references "dash.X" but table may live in "public.X"
  FOR _t IN SELECT unnest(ARRAY[
    'dash_dream_reflection_tree',
    'dash_workflow_run_history',
    'dash_dream_runs',
    'dash_dream_findings',
    'dash_dream_insights',
    'dash_anti_patterns',
    'dash_dream_digests',
    'dash_skill_library',
    'dash_agent_registry'
  ])
  LOOP
    -- Find where it actually lives (prefer public, then ai; skip if already in dash)
    SELECT table_schema INTO _real_schema
    FROM information_schema.tables
    WHERE table_name = _t
      AND table_schema IN ('public', 'dash', 'ai')
    ORDER BY CASE table_schema
      WHEN 'dash' THEN 1
      WHEN 'public' THEN 2
      WHEN 'ai' THEN 3
    END
    LIMIT 1;

    IF _real_schema IS NOT NULL AND _real_schema <> 'dash' THEN
      -- Create dash.X view → real schema, only if dash.X doesn't already exist as a table
      IF NOT EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'dash' AND table_name = _t
      ) THEN
        EXECUTE format('CREATE OR REPLACE VIEW dash.%I AS SELECT * FROM %I.%I',
                       _t, _real_schema, _t);
        RAISE NOTICE 'created view dash.% -> %.%', _t, _real_schema, _t;
      END IF;
    END IF;
  END LOOP;
END $$;
