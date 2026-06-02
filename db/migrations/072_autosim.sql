-- Migration 072: Dash AutoSim — automatic scenario spawning + recording.
--
-- Tables for the AutoSim subsystem that auto-spawns Sim Lab simulations
-- from bundled template scenario packs, grounded generators, wizard, chat
-- intents, dream reflection, drift detection, and the marketplace.
--
-- Depends on: migration 038 (dash.sim_projects).
-- Idempotent: safe to re-run.

-- 1. dash.dash_autosim_packs — read-only catalog of bundled scenario packs.
CREATE TABLE IF NOT EXISTS dash.dash_autosim_packs (
    id              BIGSERIAL PRIMARY KEY,
    vertical        TEXT          NOT NULL,
    pack_name       TEXT          NOT NULL,
    scenarios       JSONB         NOT NULL,
    version         INT           DEFAULT 1,
    status          TEXT          DEFAULT 'active',
    created_at      TIMESTAMPTZ   DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_autosim_packs
    ON dash.dash_autosim_packs(vertical, pack_name, version);


-- 2. dash.dash_autosim_runs — every auto-spawned simulation recorded.
CREATE TABLE IF NOT EXISTS dash.dash_autosim_runs (
    id                BIGSERIAL PRIMARY KEY,
    project_slug      TEXT          NOT NULL,
    scenario_source   TEXT          NOT NULL,
        -- bundled | grounded | wizard | chat | dream | drift | marketplace
    scenario          JSONB         NOT NULL,
    sim_project_id    TEXT,
        -- FK soft-ref to dash.sim_projects.id, nullable until created
    trigger_source    TEXT,
        -- template_apply | training_complete | user_chat | cron | api
    trigger_user_id   INT,
    status            TEXT          DEFAULT 'queued',
        -- queued | running | done | failed
    cost_usd          NUMERIC(10,4) DEFAULT 0,
    error             TEXT,
    created_at        TIMESTAMPTZ   DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_autosim_runs_proj
    ON dash.dash_autosim_runs(project_slug, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_autosim_runs_status
    ON dash.dash_autosim_runs(status, created_at DESC);


-- 3. dash.dash_autosim_briefs — morning / alert / comparison / drift digests.
CREATE TABLE IF NOT EXISTS dash.dash_autosim_briefs (
    id              BIGSERIAL PRIMARY KEY,
    project_slug    TEXT          NOT NULL,
    user_id         INT,
    brief_type      TEXT          NOT NULL,
        -- morning | alert | comparison | drift
    body_md         TEXT          NOT NULL,
    sim_run_ids     BIGINT[],
    read_at         TIMESTAMPTZ,
    created_at      TIMESTAMPTZ   DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_autosim_briefs_unread
    ON dash.dash_autosim_briefs(project_slug, user_id, created_at DESC)
    WHERE read_at IS NULL;


-- 4. dash.dash_sim_recommendations — anonymized marketplace popularity.
CREATE TABLE IF NOT EXISTS dash.dash_sim_recommendations (
    id                    BIGSERIAL PRIMARY KEY,
    source                TEXT          NOT NULL,
        -- popularity | dream | drift | curated
    vertical              TEXT,
    title                 TEXT          NOT NULL,
    scenario_template     JSONB         NOT NULL,
        -- entity names hashed for anonymity
    run_count             INT           DEFAULT 0,
    unique_tenants        INT           DEFAULT 0,
    last_recommended_at   TIMESTAMPTZ,
    created_at            TIMESTAMPTZ   DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_sim_recs_popularity
    ON dash.dash_sim_recommendations(vertical, run_count DESC, unique_tenants DESC);


-- 5. dash.dash_sim_comparison_runs — W6.H 3-variant comparison harness.
CREATE TABLE IF NOT EXISTS dash.dash_sim_comparison_runs (
    id                     BIGSERIAL PRIMARY KEY,
    project_slug           TEXT          NOT NULL,
    base_scenario          JSONB         NOT NULL,
    optimistic_sim_id      TEXT,
    baseline_sim_id        TEXT,
    pessimistic_sim_id     TEXT,
    status                 TEXT          DEFAULT 'running',
    created_by_user_id     INT,
    created_at             TIMESTAMPTZ   DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_sim_comparison_proj
    ON dash.dash_sim_comparison_runs(project_slug, created_at DESC);


-- 6. Extend dash.sim_projects with a source column (idempotent ADD).
ALTER TABLE dash.sim_projects
    ADD COLUMN IF NOT EXISTS source TEXT DEFAULT 'manual';


-- ROLLBACK
-- To revert this migration, run the statements below in order:
--
-- ALTER TABLE dash.sim_projects DROP COLUMN IF EXISTS source;
--
-- DROP INDEX IF EXISTS dash.idx_sim_comparison_proj;
-- DROP TABLE IF EXISTS dash.dash_sim_comparison_runs CASCADE;
--
-- DROP INDEX IF EXISTS dash.idx_sim_recs_popularity;
-- DROP TABLE IF EXISTS dash.dash_sim_recommendations CASCADE;
--
-- DROP INDEX IF EXISTS dash.idx_autosim_briefs_unread;
-- DROP TABLE IF EXISTS dash.dash_autosim_briefs CASCADE;
--
-- DROP INDEX IF EXISTS dash.idx_autosim_runs_status;
-- DROP INDEX IF EXISTS dash.idx_autosim_runs_proj;
-- DROP TABLE IF EXISTS dash.dash_autosim_runs CASCADE;
--
-- DROP INDEX IF EXISTS dash.uq_autosim_packs;
-- DROP TABLE IF EXISTS dash.dash_autosim_packs CASCADE;
