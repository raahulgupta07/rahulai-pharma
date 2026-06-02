-- Migration 066: Dream Reflection (Phase 1)
--
-- Nightly session-replay → insights + anti-patterns pipeline.
-- Captures each Dream Reflection cycle execution (dash_dream_runs),
-- the findings produced per run with approval lifecycle (dash_dream_findings),
-- the ExpeL vote-weighted insight pool capped at 200/project
-- (dash_dream_insights), and promoted anti-patterns injected as
-- Context Layer 14 (dash_anti_patterns).
--
-- NOTE: depends on the `dash` schema existing — created by migration 001.
-- Idempotent (CREATE TABLE IF NOT EXISTS, CREATE INDEX IF NOT EXISTS).

-- 1. dash_dream_runs — one row per Dream Reflection cycle execution
CREATE TABLE IF NOT EXISTS dash.dash_dream_runs (
    id                BIGSERIAL PRIMARY KEY,
    project_slug      TEXT          NOT NULL,
    started_at        TIMESTAMPTZ   NOT NULL DEFAULT now(),
    finished_at       TIMESTAMPTZ,
    sessions_scanned  INT           DEFAULT 0,
    findings_count    INT           DEFAULT 0,
    cost_usd          NUMERIC(10,4) DEFAULT 0,
    status            TEXT          NOT NULL DEFAULT 'running',
        -- running | done | failed | skipped_budget
    error             TEXT,
    window_hours      INT           DEFAULT 24,
    trigger           TEXT          DEFAULT 'cron'
        -- cron | manual | api
);

CREATE INDEX IF NOT EXISTS idx_dream_runs_project_started
    ON dash.dash_dream_runs(project_slug, started_at DESC);

CREATE INDEX IF NOT EXISTS idx_dream_runs_status_started
    ON dash.dash_dream_runs(status, started_at DESC);

-- 2. dash_dream_findings — output items per run, w/ approval lifecycle
CREATE TABLE IF NOT EXISTS dash.dash_dream_findings (
    id                  BIGSERIAL PRIMARY KEY,
    run_id              BIGINT        REFERENCES dash.dash_dream_runs(id) ON DELETE CASCADE,
    project_slug        TEXT          NOT NULL,
    finding_type        TEXT          NOT NULL,
        -- decision_rule | anti_pattern | user_persona_delta
        -- workflow_candidate | skill_patch_candidate
        -- curiosity_seed | knowledge_gap
    content             JSONB         NOT NULL,
    confidence          NUMERIC(3,2)  NOT NULL,
    status              TEXT          NOT NULL DEFAULT 'pending',
        -- pending | approved | rejected | auto_promoted | reverted
    target_table        TEXT,
    target_id           BIGINT,
    finding_hash        TEXT          NOT NULL,
        -- sha256 first 32 chars for dedup
    source_session_ids  TEXT[],
    reviewed_by         TEXT,
    reviewed_at         TIMESTAMPTZ,
    created_at          TIMESTAMPTZ   NOT NULL DEFAULT now(),
    CONSTRAINT uq_dream_findings_project_hash UNIQUE (project_slug, finding_hash)
);

CREATE INDEX IF NOT EXISTS idx_dream_findings_project_status_created
    ON dash.dash_dream_findings(project_slug, status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_dream_findings_run
    ON dash.dash_dream_findings(run_id);

CREATE INDEX IF NOT EXISTS idx_dream_findings_type
    ON dash.dash_dream_findings(finding_type);

-- 3. dash_dream_insights — ExpeL vote-weighted insight pool, capped 200/project
CREATE TABLE IF NOT EXISTS dash.dash_dream_insights (
    id                    BIGSERIAL PRIMARY KEY,
    project_slug          TEXT          NOT NULL,
    insight               TEXT          NOT NULL,
    evidence_episode_ids  TEXT[],
    upvotes               INT           NOT NULL DEFAULT 0,
    downvotes             INT           NOT NULL DEFAULT 0,
    last_used_at          TIMESTAMPTZ,
    status                TEXT          NOT NULL DEFAULT 'active',
        -- active | deprecated | reverted
    source_dream_run_id   BIGINT        REFERENCES dash.dash_dream_runs(id) ON DELETE SET NULL,
    insight_hash          TEXT          NOT NULL,
    created_at            TIMESTAMPTZ   NOT NULL DEFAULT now(),
    CONSTRAINT uq_dream_insights_project_hash UNIQUE (project_slug, insight_hash)
);

CREATE INDEX IF NOT EXISTS idx_dream_insights_project_status_upvotes
    ON dash.dash_dream_insights(project_slug, status, upvotes DESC);

CREATE INDEX IF NOT EXISTS idx_dream_insights_project_last_used
    ON dash.dash_dream_insights(project_slug, last_used_at DESC NULLS LAST);

-- 4. dash_anti_patterns — promoted from findings, injected as Context Layer 14
CREATE TABLE IF NOT EXISTS dash.dash_anti_patterns (
    id                       BIGSERIAL PRIMARY KEY,
    project_slug             TEXT,                       -- NULL = global across all projects
    pattern                  TEXT          NOT NULL,
    why_bad                  TEXT          NOT NULL,
    example_failure          TEXT,
    confidence               NUMERIC(3,2)  DEFAULT 0.8,
    source_dream_finding_id  BIGINT        REFERENCES dash.dash_dream_findings(id) ON DELETE SET NULL,
    hit_count                INT           NOT NULL DEFAULT 0,
    score_before             NUMERIC(3,2),               -- judge avg before injection (A/B revert check)
    score_after              NUMERIC(3,2),               -- judge avg after
    status                   TEXT          NOT NULL DEFAULT 'active',
        -- active | reverted | archived
    created_at               TIMESTAMPTZ   NOT NULL DEFAULT now(),
    reverted_at              TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_anti_patterns_project_confidence_active
    ON dash.dash_anti_patterns(project_slug, confidence DESC)
    WHERE status = 'active';

-- ROLLBACK
-- To revert this migration, run the statements below (drop in reverse FK order):
--
-- DROP TABLE IF EXISTS dash.dash_anti_patterns CASCADE;
-- DROP TABLE IF EXISTS dash.dash_dream_insights CASCADE;
-- DROP TABLE IF EXISTS dash.dash_dream_findings CASCADE;
-- DROP TABLE IF EXISTS dash.dash_dream_runs CASCADE;
