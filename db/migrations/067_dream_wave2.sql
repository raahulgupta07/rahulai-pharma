-- Migration 067: Dream Reflection (Phases 2-5)
--
-- Wave-2 expansion of the Dream Reflection subsystem (depends on 066).
-- Adds bi-temporal columns to brain + knowledge tables (Graphiti pattern:
-- never delete, only invalidate/supersede), plus four new tables:
--   * dash_skill_library        — Voyager-style parameterized SQL recipes
--   * dash_dream_digests        — wiki markdown per nightly run (Devin pattern)
--   * dash_dream_personas       — per-user persona block (Letta MemoryBlock)
--   * dash_dream_reflection_tree — Generative Agents reflection hierarchy
-- Also extends dash_anti_patterns with A/B observation-window metadata.
--
-- NOTE: depends on migrations 001 (dash schema), 066 (dream tables),
--       and the pre-existing dash_company_brain + dash_knowledge_triples
--       tables. Idempotent (ADD COLUMN IF NOT EXISTS, CREATE TABLE IF
--       NOT EXISTS, CREATE INDEX IF NOT EXISTS).

-- 1. Bi-temporal columns on dash_company_brain (Graphiti pattern)
-- Schema-detect DO block: table may live in `public` or `dash` depending on
-- install history. Skip silently if missing (e.g. fresh demo DBs without brain).
DO $$
DECLARE _schema TEXT;
BEGIN
  SELECT table_schema INTO _schema FROM information_schema.tables
   WHERE table_name = 'dash_company_brain'
     AND table_schema IN ('dash', 'public', 'ai')
   ORDER BY CASE table_schema WHEN 'dash' THEN 1 WHEN 'public' THEN 2 ELSE 3 END
   LIMIT 1;
  IF _schema IS NOT NULL THEN
    EXECUTE format(
      'ALTER TABLE %I.dash_company_brain
         ADD COLUMN IF NOT EXISTS valid_at      TIMESTAMPTZ,
         ADD COLUMN IF NOT EXISTS invalid_at    TIMESTAMPTZ,
         ADD COLUMN IF NOT EXISTS expired_at    TIMESTAMPTZ,
         ADD COLUMN IF NOT EXISTS superseded_by BIGINT',
      _schema);
    EXECUTE format(
      'CREATE INDEX IF NOT EXISTS idx_brain_active_%s
         ON %I.dash_company_brain(project_slug) WHERE expired_at IS NULL',
      _schema, _schema);
  ELSE
    RAISE NOTICE 'dash_company_brain not found in any schema — bi-temporal cols skipped';
  END IF;
END $$;

-- 2. Bi-temporal columns on dash_knowledge_triples (Graphiti pattern)
DO $$
DECLARE _schema TEXT;
BEGIN
  SELECT table_schema INTO _schema FROM information_schema.tables
   WHERE table_name = 'dash_knowledge_triples'
     AND table_schema IN ('dash', 'public', 'ai')
   ORDER BY CASE table_schema WHEN 'dash' THEN 1 WHEN 'public' THEN 2 ELSE 3 END
   LIMIT 1;
  IF _schema IS NOT NULL THEN
    EXECUTE format(
      'ALTER TABLE %I.dash_knowledge_triples
         ADD COLUMN IF NOT EXISTS valid_at      TIMESTAMPTZ,
         ADD COLUMN IF NOT EXISTS invalid_at    TIMESTAMPTZ,
         ADD COLUMN IF NOT EXISTS expired_at    TIMESTAMPTZ,
         ADD COLUMN IF NOT EXISTS superseded_by BIGINT',
      _schema);
    EXECUTE format(
      'CREATE INDEX IF NOT EXISTS idx_kg_active_%s
         ON %I.dash_knowledge_triples(project_slug) WHERE expired_at IS NULL',
      _schema, _schema);
  ELSE
    RAISE NOTICE 'dash_knowledge_triples not found in any schema — bi-temporal cols skipped';
  END IF;
END $$;

-- 3. dash_skill_library — Voyager-style parameterized SQL recipes
CREATE TABLE IF NOT EXISTS dash.dash_skill_library (
    id                         BIGSERIAL PRIMARY KEY,
    project_slug               TEXT          NOT NULL,
    name                       TEXT          NOT NULL,
        -- short slug e.g. "monthly_revenue_by_region"
    description                TEXT          NOT NULL,
        -- NL description, embed-friendly
    sql_template               TEXT          NOT NULL,
        -- SQL with {param} placeholders
    params_schema              JSONB         DEFAULT '{}'::jsonb,
        -- {param_name: {type, required, default}}
    description_embedding      TEXT,
        -- pgvector serialized (1536-dim), nullable until embed daemon runs
    success_count              INT           NOT NULL DEFAULT 0,
    failure_count              INT           NOT NULL DEFAULT 0,
    avg_judge_score            NUMERIC(3,2),
    source_query_pattern_id    BIGINT,
        -- link back to dash_query_patterns if promoted
    source_dream_run_id        BIGINT        REFERENCES dash.dash_dream_runs(id) ON DELETE SET NULL,
    status                     TEXT          NOT NULL DEFAULT 'active',
        -- active | deprecated | reverted
    last_used_at               TIMESTAMPTZ,
    created_at                 TIMESTAMPTZ   NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_skill_lib_active
    ON dash.dash_skill_library(project_slug, status)
    WHERE status = 'active';

CREATE INDEX IF NOT EXISTS idx_skill_lib_score
    ON dash.dash_skill_library(project_slug, success_count DESC, avg_judge_score DESC NULLS LAST);

CREATE UNIQUE INDEX IF NOT EXISTS uq_skill_lib_name
    ON dash.dash_skill_library(project_slug, name);

-- 4. dash_dream_digests — wiki markdown per nightly run (Devin pattern)
CREATE TABLE IF NOT EXISTS dash.dash_dream_digests (
    id               BIGSERIAL PRIMARY KEY,
    run_id           BIGINT        REFERENCES dash.dash_dream_runs(id) ON DELETE CASCADE,
    project_slug     TEXT          NOT NULL,
    digest_date      DATE          NOT NULL,
    title            TEXT,
    body_md          TEXT          NOT NULL,
        -- full markdown for browsing
    changes_summary  JSONB,
        -- {anti_patterns_added, facts_invalidated, skills_promoted, ...}
    file_path        TEXT,
        -- knowledge/{slug}/dreams/{date}.md (optional disk artifact)
    created_at       TIMESTAMPTZ   NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_dream_digests_date
    ON dash.dash_dream_digests(project_slug, digest_date DESC);

CREATE UNIQUE INDEX IF NOT EXISTS uq_dream_digests
    ON dash.dash_dream_digests(project_slug, digest_date);

-- 5. dash_dream_personas — per-user persona block (Letta MemoryBlock analog)
CREATE TABLE IF NOT EXISTS dash.dash_dream_personas (
    id                     BIGSERIAL PRIMARY KEY,
    project_slug           TEXT          NOT NULL,
    user_id                INT           NOT NULL,
    persona                JSONB         NOT NULL DEFAULT '{}'::jsonb,
        -- {traits[], style, preferences{}, ...}
    version                INT           NOT NULL DEFAULT 1,
    source_dream_run_id    BIGINT        REFERENCES dash.dash_dream_runs(id) ON DELETE SET NULL,
    updated_at             TIMESTAMPTZ   NOT NULL DEFAULT now(),
    created_at             TIMESTAMPTZ   NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_dream_personas
    ON dash.dash_dream_personas(project_slug, user_id);

-- 6. dash_dream_reflection_tree — Generative Agents pattern (insights cite insights)
CREATE TABLE IF NOT EXISTS dash.dash_dream_reflection_tree (
    id                    BIGSERIAL PRIMARY KEY,
    run_id                BIGINT        REFERENCES dash.dash_dream_runs(id) ON DELETE CASCADE,
    project_slug          TEXT          NOT NULL,
    depth                 INT           NOT NULL DEFAULT 0,
        -- 0 = leaf (raw insight), 1+ = abstract reflection
    parent_id             BIGINT        REFERENCES dash.dash_dream_reflection_tree(id) ON DELETE SET NULL,
    reflection_text       TEXT          NOT NULL,
    evidence_finding_ids  BIGINT[],
        -- which findings this reflects on
    evidence_session_ids  TEXT[],
        -- raw sessions back to source
    confidence            NUMERIC(3,2),
    created_at            TIMESTAMPTZ   NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_reflect_tree_depth
    ON dash.dash_dream_reflection_tree(project_slug, depth DESC, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_reflect_tree_parent
    ON dash.dash_dream_reflection_tree(parent_id);

-- 7. Extend dash_anti_patterns w/ A/B observation-window metadata
ALTER TABLE dash.dash_anti_patterns
    ADD COLUMN IF NOT EXISTS observation_window_started_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS observation_completed_at      TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS observation_sample_size       INT DEFAULT 0;

-- ROLLBACK
-- To revert this migration, run the statements below (drop in reverse FK order):
--
-- ALTER TABLE dash.dash_anti_patterns
--     DROP COLUMN IF EXISTS observation_window_started_at,
--     DROP COLUMN IF EXISTS observation_completed_at,
--     DROP COLUMN IF EXISTS observation_sample_size;
--
-- DROP TABLE IF EXISTS dash.dash_dream_reflection_tree CASCADE;
-- DROP TABLE IF EXISTS dash.dash_dream_personas CASCADE;
-- DROP TABLE IF EXISTS dash.dash_dream_digests CASCADE;
-- DROP TABLE IF EXISTS dash.dash_skill_library CASCADE;
--
-- For bi-temporal columns the schema is detected at apply time; rollback
-- needs to try both schemas. Wrap in DO blocks to avoid hard failures.
--
-- DO $$
-- DECLARE _s TEXT;
-- BEGIN
--   FOREACH _s IN ARRAY ARRAY['dash','public','ai'] LOOP
--     BEGIN
--       EXECUTE format('DROP INDEX IF EXISTS %I.idx_kg_active_%s', _s, _s);
--       EXECUTE format('DROP INDEX IF EXISTS %I.idx_brain_active_%s', _s, _s);
--       EXECUTE format(
--         'ALTER TABLE %I.dash_knowledge_triples
--            DROP COLUMN IF EXISTS superseded_by,
--            DROP COLUMN IF EXISTS expired_at,
--            DROP COLUMN IF EXISTS invalid_at,
--            DROP COLUMN IF EXISTS valid_at', _s);
--       EXECUTE format(
--         'ALTER TABLE %I.dash_company_brain
--            DROP COLUMN IF EXISTS superseded_by,
--            DROP COLUMN IF EXISTS expired_at,
--            DROP COLUMN IF EXISTS invalid_at,
--            DROP COLUMN IF EXISTS valid_at', _s);
--     EXCEPTION WHEN OTHERS THEN NULL;
--     END;
--   END LOOP;
-- END $$;
