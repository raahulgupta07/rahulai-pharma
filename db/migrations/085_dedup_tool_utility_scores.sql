-- 085_dedup_tool_utility_scores.sql
-- Consolidate duplicate dash_tool_utility_scores tables:
--   public.dash_tool_utility_scores (from migration 015) — per-call telemetry
--   dash.dash_tool_utility_scores   (from migration 084) — aggregated rollups
-- Goal: single source of truth at dash.dash_tool_utility_scores by superset-merging
-- the per-call telemetry columns into the dash.* table, migrating rows, then
-- dropping the public.* copy.
-- Idempotent: re-runnable.

CREATE SCHEMA IF NOT EXISTS dash;

-- 1. Ensure dash.dash_tool_utility_scores exists with aggregated columns.
CREATE TABLE IF NOT EXISTS dash.dash_tool_utility_scores (
    id              BIGSERIAL PRIMARY KEY,
    tool_name       TEXT NOT NULL,
    project_slug    TEXT,
    calls_30d       INTEGER NOT NULL DEFAULT 0,
    success_30d     INTEGER NOT NULL DEFAULT 0,
    avg_latency_ms  NUMERIC(10, 2),
    score           NUMERIC(5, 2),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 2. Extend dash.* with per-call telemetry columns from public.* (nullable).
ALTER TABLE dash.dash_tool_utility_scores
    ADD COLUMN IF NOT EXISTS agent         TEXT,
    ADD COLUMN IF NOT EXISTS user_id       INTEGER,
    ADD COLUMN IF NOT EXISTS args_hash     TEXT,
    ADD COLUMN IF NOT EXISTS success       BOOLEAN,
    ADD COLUMN IF NOT EXISTS latency_ms    INTEGER,
    ADD COLUMN IF NOT EXISTS error_class   TEXT,
    ADD COLUMN IF NOT EXISTS error_message TEXT,
    ADD COLUMN IF NOT EXISTS feedback      SMALLINT,
    ADD COLUMN IF NOT EXISTS retry_count   SMALLINT DEFAULT 0,
    ADD COLUMN IF NOT EXISTS ts            TIMESTAMPTZ DEFAULT now();

-- 3. Indexes mirroring both originals (idempotent).
CREATE INDEX IF NOT EXISTS idx_tool_utility_tool_score
    ON dash.dash_tool_utility_scores (tool_name, score DESC);
CREATE INDEX IF NOT EXISTS idx_tool_utility_project_score
    ON dash.dash_tool_utility_scores (project_slug, score DESC);
CREATE INDEX IF NOT EXISTS idx_tool_utility_tool_proj_ts
    ON dash.dash_tool_utility_scores(tool_name, project_slug, ts DESC);
CREATE INDEX IF NOT EXISTS idx_tool_utility_proj_ts
    ON dash.dash_tool_utility_scores(project_slug, ts DESC);
CREATE INDEX IF NOT EXISTS idx_tool_utility_success
    ON dash.dash_tool_utility_scores(tool_name, success);

-- 4. Migrate rows from public.* if it still exists, then drop it.
DO $$
DECLARE
    v_migrated INTEGER := 0;
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'dash_tool_utility_scores'
    ) THEN
        INSERT INTO dash.dash_tool_utility_scores
            (tool_name, agent, project_slug, user_id, args_hash,
             success, latency_ms, error_class, error_message,
             feedback, retry_count, ts)
        SELECT
            tool_name, agent, project_slug, user_id, args_hash,
            success, latency_ms, error_class, error_message,
            feedback, retry_count, ts
        FROM public.dash_tool_utility_scores
        ON CONFLICT DO NOTHING;

        GET DIAGNOSTICS v_migrated = ROW_COUNT;
        RAISE NOTICE '085: migrated % row(s) from public.dash_tool_utility_scores -> dash.dash_tool_utility_scores', v_migrated;

        DROP TABLE public.dash_tool_utility_scores CASCADE;
        RAISE NOTICE '085: dropped public.dash_tool_utility_scores';
    ELSE
        RAISE NOTICE '085: public.dash_tool_utility_scores does not exist; nothing to migrate';
    END IF;
END $$;

COMMENT ON TABLE dash.dash_tool_utility_scores IS
    'SkillRefinery: unified per-call telemetry + aggregated utility scores. Consolidated from public.* in migration 085.';
