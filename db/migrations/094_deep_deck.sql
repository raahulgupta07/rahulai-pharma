-- 094_deep_deck.sql
-- Deep Deck pipeline audit trail. 6-stage research-then-present flow
-- (Ingest → Gaps → Plan → Execute → Synthesize → Build).
-- All tables idempotent (CREATE TABLE IF NOT EXISTS / ADD COLUMN IF NOT EXISTS).

-- Run-level audit
CREATE TABLE IF NOT EXISTS dash.dash_deep_deck_runs (
    id              BIGSERIAL PRIMARY KEY,
    project_slug    TEXT NOT NULL,
    user_id         INT,
    session_id      TEXT,                              -- UUID string (chat session)
    status          TEXT NOT NULL DEFAULT 'running',   -- running | done | failed | cancelled
    current_stage   TEXT,                              -- ingest | gaps | plan | execute | synthesize | build
    stage_progress  INT DEFAULT 0,                     -- 0..6
    pres_id         BIGINT,                            -- FK-style link to dash_presentations after stage 6
    cost_usd        NUMERIC(10,4) DEFAULT 0,
    error_text      TEXT,
    config          JSONB NOT NULL DEFAULT '{}'::jsonb, -- {ml_augment, web_benchmark, counter_hypothesis, ...}
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_dd_runs_project ON dash.dash_deep_deck_runs(project_slug);
CREATE INDEX IF NOT EXISTS idx_dd_runs_status ON dash.dash_deep_deck_runs(status);
CREATE INDEX IF NOT EXISTS idx_dd_runs_started ON dash.dash_deep_deck_runs(started_at DESC);

-- Stage 2 output: gap questions identified
CREATE TABLE IF NOT EXISTS dash.dash_deep_deck_gaps (
    id          BIGSERIAL PRIMARY KEY,
    run_id      BIGINT NOT NULL,
    rank        INT NOT NULL,
    question    TEXT NOT NULL,
    rationale   TEXT,                  -- why this gap matters
    priority    NUMERIC(3,2),          -- 0-1
    status      TEXT DEFAULT 'pending', -- pending | planned | executed | skipped | failed
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_dd_gaps_run ON dash.dash_deep_deck_gaps(run_id);

-- Stage 3-4: query plans + results
CREATE TABLE IF NOT EXISTS dash.dash_deep_deck_queries (
    id              BIGSERIAL PRIMARY KEY,
    run_id          BIGINT NOT NULL,
    gap_id          BIGINT,           -- which gap this query answers
    rank            INT,
    question        TEXT NOT NULL,
    sql_text        TEXT NOT NULL,
    expected_shape  TEXT,             -- e.g. "1 row × 3 cols" or "trend over time"
    status          TEXT DEFAULT 'pending', -- pending | executed | failed | timeout
    row_count       INT,
    columns         JSONB,
    rows_preview    JSONB,            -- first 20 rows for synth + slide
    error_text      TEXT,
    duration_ms     INT,
    executed_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_dd_queries_run ON dash.dash_deep_deck_queries(run_id);
CREATE INDEX IF NOT EXISTS idx_dd_queries_gap ON dash.dash_deep_deck_queries(gap_id);
