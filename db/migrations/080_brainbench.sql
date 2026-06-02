-- Migration 080: BrainBench-Real — full session capture + A/B replay
--
-- Extends partial `dash_eval_runs` with full session capture (question +
-- context snapshot + tools called + final answer + judge score) and an
-- A/B replay engine that re-runs captured corpora through the current
-- dream-cycle / skill / brain state and detects regressions vs wins.
--
-- Schema-qualified `dash.*`. JSONB columns sized for: context_snapshot
-- (~50KB), tools_called (~5KB), results/summary (variable).
--
-- Idempotent (IF NOT EXISTS).

CREATE SCHEMA IF NOT EXISTS dash;

-- ─────────────────────────────────────────────────────────────────────────
-- 1. Corpus — captured sessions, the "ground truth" for replay
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS dash.dash_brainbench_corpus (
    id                    BIGSERIAL PRIMARY KEY,
    project_slug          TEXT         NOT NULL,
    session_id            TEXT         NOT NULL,
    question              TEXT         NOT NULL,
    expected_answer       TEXT,
        -- canonical answer (curator-edited) — falls back to original_answer
    context_snapshot      JSONB        NOT NULL DEFAULT '{}'::jsonb,
        -- frozen state of all 16 context layers at capture time
    tools_called          JSONB        NOT NULL DEFAULT '[]'::jsonb,
        -- list of {tool, args, result_shape} extracted from run
    original_answer       TEXT,
        -- the answer the team actually produced at capture time
    original_judge_score  NUMERIC(3,2),
        -- score from dash_quality_scores at capture time
    original_run_at       TIMESTAMPTZ,
        -- when the original chat happened
    tags                  TEXT[]       DEFAULT ARRAY[]::TEXT[],
    created_at            TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_brainbench_corpus_project_recent
    ON dash.dash_brainbench_corpus (project_slug, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_brainbench_corpus_session
    ON dash.dash_brainbench_corpus (session_id);

-- ─────────────────────────────────────────────────────────────────────────
-- 2. Runs — one row per A/B replay invocation
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS dash.dash_brainbench_runs (
    id            BIGSERIAL PRIMARY KEY,
    project_slug  TEXT         NOT NULL,
    corpus_ids    BIGINT[]     NOT NULL DEFAULT ARRAY[]::BIGINT[],
    run_label     TEXT,
        -- human label: "post-dream-cycle-2026-05-17" / "skill-patch-X"
    run_config    JSONB        DEFAULT '{}'::jsonb,
        -- captures runtime knobs: cycle version, active skills, brain version
    results       JSONB        DEFAULT '[]'::jsonb,
        -- denormalized result list (also in dash_brainbench_results rows)
    summary       JSONB        DEFAULT '{}'::jsonb,
        -- {wins, regressions, ties, avg_delta, top_wins, top_regressions}
    started_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    finished_at   TIMESTAMPTZ,
    status        TEXT         NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued','running','done','failed'))
);

CREATE INDEX IF NOT EXISTS idx_brainbench_runs_project_recent
    ON dash.dash_brainbench_runs (project_slug, started_at DESC);

-- ─────────────────────────────────────────────────────────────────────────
-- 3. Per-result rows — one row per (run × corpus item)
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS dash.dash_brainbench_results (
    id                   BIGSERIAL PRIMARY KEY,
    run_id               BIGINT       NOT NULL,
        -- FK omitted intentionally — results survive run row deletion (audit)
    corpus_id            BIGINT       NOT NULL,
    replayed_answer      TEXT,
    replayed_judge_score NUMERIC(3,2),
    score_delta          NUMERIC(3,2),
        -- replayed - original; positive = win, negative = regression
    regression           BOOLEAN      DEFAULT FALSE,
    win                  BOOLEAN      DEFAULT FALSE,
    error                TEXT,
        -- non-null when replay failed for this single corpus item
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_brainbench_results_run
    ON dash.dash_brainbench_results (run_id);

CREATE INDEX IF NOT EXISTS idx_brainbench_results_corpus
    ON dash.dash_brainbench_results (corpus_id);

-- ─────────────────────────────────────────────────────────────────────────
-- 4. Daily cron — auto-capture high-quality sessions (≥4.5 judge in 24h)
-- ─────────────────────────────────────────────────────────────────────────
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema='dash' AND table_name='dash_agent_schedules'
  ) AND EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema='dash' AND table_name='dash_agent_schedules'
      AND column_name='agent_target'
  ) THEN
    INSERT INTO dash.dash_agent_schedules (
      id, project_slug, created_by, created_by_agent,
      name, description, schedule_kind, cron_expr, interval_seconds,
      next_run_at, prompt, agent_target, enabled, max_runs
    ) VALUES (
      'sch_brainbench001',
      NULL,
      0,
      'system',
      'BrainBench auto-capture daily',
      'Capture high-quality chat sessions (judge >= 4.5) into the BrainBench corpus for reproducible A/B replay. Caps 50 sessions/project/day.',
      'cron',
      '0 5 * * *',
      NULL,
      now() + INTERVAL '1 day',
      'Run the BrainBench auto-capture cycle: enqueue brainbench_auto_capture minion (single global run that scans all projects for last-24h sessions with judge >= 4.5 and snapshots them into dash_brainbench_corpus, cap 50/project/day).',
      'leader',
      true,
      NULL
    ) ON CONFLICT (id) DO NOTHING;
  ELSE
    RAISE NOTICE 'dash_agent_schedules schema mismatch — sch_brainbench001 skipped';
  END IF;
END $$;

-- ROLLBACK
-- DELETE FROM dash.dash_agent_schedules WHERE id='sch_brainbench001';
-- DROP INDEX IF EXISTS dash.idx_brainbench_results_corpus;
-- DROP INDEX IF EXISTS dash.idx_brainbench_results_run;
-- DROP TABLE  IF EXISTS dash.dash_brainbench_results;
-- DROP INDEX IF EXISTS dash.idx_brainbench_runs_project_recent;
-- DROP TABLE  IF EXISTS dash.dash_brainbench_runs;
-- DROP INDEX IF EXISTS dash.idx_brainbench_corpus_session;
-- DROP INDEX IF EXISTS dash.idx_brainbench_corpus_project_recent;
-- DROP TABLE  IF EXISTS dash.dash_brainbench_corpus;
