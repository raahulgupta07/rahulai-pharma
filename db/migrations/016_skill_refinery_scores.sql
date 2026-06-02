-- 016_skill_refinery_scores.sql — aggregated tool scores (per project + global).
-- Idempotent.

CREATE TABLE IF NOT EXISTS public.dash_tool_scores (
    id              BIGSERIAL PRIMARY KEY,
    tool_name       TEXT NOT NULL,
    project_slug    TEXT,                    -- NULL = global aggregate
    score           NUMERIC(5,2) NOT NULL,   -- 0..100
    success_rate    NUMERIC(5,2),            -- 0..100
    feedback_score  NUMERIC(5,2),            -- 0..100
    latency_p50_ms  INTEGER,
    latency_p95_ms  INTEGER,
    calls           INTEGER NOT NULL DEFAULT 0,
    fails           INTEGER NOT NULL DEFAULT 0,
    last_error      TEXT,
    window_days     INTEGER NOT NULL DEFAULT 14,
    computed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tool_name, project_slug)
);

CREATE INDEX IF NOT EXISTS idx_tool_scores_proj
    ON public.dash_tool_scores(project_slug, score);

COMMENT ON TABLE public.dash_tool_scores IS
    'SkillRefinery: rolling tool utility score per project. Recomputed nightly.';
