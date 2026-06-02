-- Migration 068: Dream Reflection Advanced — Tier 1 poignancy + Tier 2 dream-lite
--
-- Adds chat-time triggered tables for between-turn reflection (vs nightly):
--   * dash_episode_buffer        — per-turn poignancy log (rolling LRU 1000/proj)
--   * dash_dream_precompute_cache — anticipated-query result cache (TTL 4h)
--   * dash_dream_lite_runs       — Tier 2 between-turn run log
--
-- These tables are NOT part of the nightly Dream Reflection cycle (066/067);
-- they're populated from chat hot-paths + minion handlers
-- (poignancy_capture, dream_lite, precompute_queries).
--
-- NOTE: depends on migrations 001 (dash schema), 066, 067. Idempotent.

-- 1. dash_episode_buffer — Tier 1 per-turn poignancy log (rolling, 1000/proj LRU)
CREATE TABLE IF NOT EXISTS dash.dash_episode_buffer (
    id                BIGSERIAL PRIMARY KEY,
    session_id        TEXT          NOT NULL,
    turn_id           INT,
    project_slug      TEXT          NOT NULL,
    user_id           INT,
    poignancy         INT           NOT NULL DEFAULT 1,
        -- 1-10 rule-based score
    question          TEXT,
    response_summary  TEXT,
    tools_used        TEXT[],
    succeeded         BOOLEAN       DEFAULT true,
    judge_score       NUMERIC(3,2),
    user_reaction     TEXT,
        -- thanks | correction | repeat | neutral | unknown
    embedding         TEXT,
        -- pgvector serialized, nullable
    consumed_at       TIMESTAMPTZ,
        -- when Tier 2 ate it
    created_at        TIMESTAMPTZ   NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_episode_buffer_unconsumed
    ON dash.dash_episode_buffer(project_slug, created_at DESC)
    WHERE consumed_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_episode_buffer_session
    ON dash.dash_episode_buffer(session_id, created_at DESC);

-- 2. dash_dream_precompute_cache — Tier 2 anticipated-query results (TTL 4h)
CREATE TABLE IF NOT EXISTS dash.dash_dream_precompute_cache (
    id                  BIGSERIAL PRIMARY KEY,
    project_slug        TEXT          NOT NULL,
    user_id             INT,
    question_hash       TEXT          NOT NULL,
        -- sha256(normalized question)
    question_text       TEXT          NOT NULL,
    sql                 TEXT,
    result_json         JSONB,
    result_summary      TEXT,
        -- short text version for Layer 16 inject
    ttl_until           TIMESTAMPTZ   NOT NULL,
    hit_count           INT           NOT NULL DEFAULT 0,
    last_hit_at         TIMESTAMPTZ,
    source_session_id   TEXT,
        -- session that prompted precompute
    created_at          TIMESTAMPTZ   NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_precompute_question
    ON dash.dash_dream_precompute_cache(project_slug, question_hash);

-- Plain index (NOT partial — PG rejects now() in partial index predicates
-- as non-IMMUTABLE). Range scans WHERE ttl_until > now() still hit it.
CREATE INDEX IF NOT EXISTS idx_precompute_ttl
    ON dash.dash_dream_precompute_cache(ttl_until);

CREATE INDEX IF NOT EXISTS idx_precompute_proj_user
    ON dash.dash_dream_precompute_cache(project_slug, user_id, last_hit_at DESC NULLS LAST);

-- 3. dash_dream_lite_runs — Tier 2 between-turn run log
CREATE TABLE IF NOT EXISTS dash.dash_dream_lite_runs (
    id                    BIGSERIAL PRIMARY KEY,
    project_slug          TEXT          NOT NULL,
    user_id               INT,
    session_id            TEXT          NOT NULL,
    triggered_at          TIMESTAMPTZ   NOT NULL DEFAULT now(),
    finished_at           TIMESTAMPTZ,
    trigger_reason        TEXT,
        -- poignancy_threshold | n_step | idle_debounce | manual
    episodes_consumed     INT           DEFAULT 0,
    persona_updated       BOOLEAN       DEFAULT false,
    precompute_queued     INT           DEFAULT 0,
    memory_ops_applied    INT           DEFAULT 0,
    cost_usd              NUMERIC(10,4) DEFAULT 0,
    status                TEXT          DEFAULT 'running',
    error                 TEXT
);

CREATE INDEX IF NOT EXISTS idx_lite_runs_project_time
    ON dash.dash_dream_lite_runs(project_slug, triggered_at DESC);

CREATE INDEX IF NOT EXISTS idx_lite_runs_session
    ON dash.dash_dream_lite_runs(session_id, triggered_at DESC);

-- ROLLBACK
-- To revert this migration, run the statements below:
--
-- DROP INDEX IF EXISTS dash.idx_lite_runs_session;
-- DROP INDEX IF EXISTS dash.idx_lite_runs_project_time;
-- DROP TABLE IF EXISTS dash.dash_dream_lite_runs CASCADE;
--
-- DROP INDEX IF EXISTS dash.idx_precompute_proj_user;
-- DROP INDEX IF EXISTS dash.idx_precompute_ttl;
-- DROP INDEX IF EXISTS dash.uq_precompute_question;
-- DROP TABLE IF EXISTS dash.dash_dream_precompute_cache CASCADE;
--
-- DROP INDEX IF EXISTS dash.idx_episode_buffer_session;
-- DROP INDEX IF EXISTS dash.idx_episode_buffer_unconsumed;
-- DROP TABLE IF EXISTS dash.dash_episode_buffer CASCADE;
