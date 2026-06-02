-- Migration 069: A/B Revert Daemon + Bootstrap dash.dash_knowledge_triples
--
-- Two purposes:
--   1. Bootstrap `dash.dash_knowledge_triples` so migration 067's inline
--      `ALTER TABLE dash.dash_knowledge_triples` succeeds on fresh installs.
--      Schema mirrors the bi-temporal pattern used on dash_company_brain
--      (Graphiti pattern: never delete, only invalidate/supersede).
--      NOTE: a separate `public.dash_knowledge_triples` table is owned by
--      `dash/tools/knowledge_graph.py` (chat-time SPO writer). We do not
--      touch it here; this dash-schema table is the canonical home for
--      bi-temporal / source-tracked KG triples used by the dream pipeline.
--
--   2. Tables for the Dream Reflection A/B Revert daemon:
--        * dash_ab_revert_runs   — one row per cycle
--        * dash_ab_revert_events — per-item revert/keep audit
--
-- Idempotent (CREATE TABLE IF NOT EXISTS, CREATE INDEX IF NOT EXISTS).
-- Depends on: 001 (dash schema), 066 (dash_anti_patterns), 067 (anti_pattern
-- A/B observation cols).

-- 1. Bootstrap dash.dash_knowledge_triples (canonical bi-temporal KG store).
CREATE TABLE IF NOT EXISTS dash.dash_knowledge_triples (
    id              BIGSERIAL PRIMARY KEY,
    project_slug    TEXT,
    subject         TEXT          NOT NULL,
    predicate       TEXT          NOT NULL,
    object          TEXT          NOT NULL,
    confidence      NUMERIC(3,2)  DEFAULT 0.5,
    source_type     TEXT,                       -- chat | training | external | brain
    source_id       TEXT,
    source_uri      TEXT,
    -- bi-temporal cols (parallel to dash_company_brain, also ADDed by 067)
    valid_at        TIMESTAMPTZ,
    invalid_at      TIMESTAMPTZ,
    expired_at      TIMESTAMPTZ,
    superseded_by   BIGINT,
    metadata        JSONB         DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ   NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_kg_subject
    ON dash.dash_knowledge_triples(project_slug, subject);

CREATE INDEX IF NOT EXISTS idx_kg_object
    ON dash.dash_knowledge_triples(project_slug, object);

-- 067 already creates idx_kg_active conditionally; repeat IF NOT EXISTS for
-- fresh-install ordering safety.
CREATE INDEX IF NOT EXISTS idx_kg_active
    ON dash.dash_knowledge_triples(project_slug)
    WHERE expired_at IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_kg_spo
    ON dash.dash_knowledge_triples(project_slug, subject, predicate, object)
    WHERE expired_at IS NULL;

-- 2. dash_ab_revert_runs — log of A/B check cycles
CREATE TABLE IF NOT EXISTS dash.dash_ab_revert_runs (
    id                          BIGSERIAL PRIMARY KEY,
    project_slug                TEXT          NOT NULL,
    started_at                  TIMESTAMPTZ   NOT NULL DEFAULT now(),
    finished_at                 TIMESTAMPTZ,
    anti_patterns_checked       INT           DEFAULT 0,
    anti_patterns_reverted      INT           DEFAULT 0,
    skill_library_checked       INT           DEFAULT 0,
    skill_library_deprecated    INT           DEFAULT 0,
    insights_checked            INT           DEFAULT 0,
    insights_deprecated         INT           DEFAULT 0,
    status                      TEXT          DEFAULT 'running',
        -- running | done | failed
    error                       TEXT
);

CREATE INDEX IF NOT EXISTS idx_ab_revert_runs_project_time
    ON dash.dash_ab_revert_runs(project_slug, started_at DESC);

-- 3. dash_ab_revert_events — per-item revert audit
CREATE TABLE IF NOT EXISTS dash.dash_ab_revert_events (
    id              BIGSERIAL PRIMARY KEY,
    run_id          BIGINT        REFERENCES dash.dash_ab_revert_runs(id) ON DELETE CASCADE,
    project_slug    TEXT          NOT NULL,
    target_type     TEXT          NOT NULL,    -- anti_pattern | skill | insight
    target_id       BIGINT        NOT NULL,
    score_before    NUMERIC(3,2),
    score_after     NUMERIC(3,2),
    delta           NUMERIC(3,2),
    sample_size     INT,
    decision        TEXT          NOT NULL,    -- reverted | kept | insufficient_data
    reason          TEXT,
    created_at      TIMESTAMPTZ   NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ab_events_project_target
    ON dash.dash_ab_revert_events(project_slug, target_type, created_at DESC);

-- ROLLBACK
-- To revert this migration, run the statements below (drop in reverse FK order):
--
-- DROP TABLE IF EXISTS dash.dash_ab_revert_events CASCADE;
-- DROP TABLE IF EXISTS dash.dash_ab_revert_runs CASCADE;
--
-- DROP INDEX IF EXISTS dash.uq_kg_spo;
-- DROP INDEX IF EXISTS dash.idx_kg_object;
-- DROP INDEX IF EXISTS dash.idx_kg_subject;
-- -- idx_kg_active is owned by 067; do not drop here.
-- -- dash.dash_knowledge_triples itself is left in place — other modules
-- -- (dream bi-temporal reconcile) depend on it.
