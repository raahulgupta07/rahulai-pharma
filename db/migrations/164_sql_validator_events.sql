-- 164_sql_validator_events.sql
-- Telemetry table for SQL validator + drift gate events.
-- Idempotent. Records auto_fix / qa_drop / chat_autofix / reject events
-- so frontend can surface counters + recent activity from the validator
-- pipeline (shipped earlier 2026-05-27).

CREATE TABLE IF NOT EXISTS dash.dash_sql_validator_events (
  id BIGSERIAL PRIMARY KEY,
  ts TIMESTAMPTZ NOT NULL DEFAULT now(),
  project_slug TEXT,
  kind TEXT NOT NULL,  -- 'auto_fix' | 'qa_drop' | 'chat_autofix' | 'reject'
  source TEXT,         -- 'qa_gen' | 'chat_runtime' | 'metrics_api' | 'workflow_runner' | 'deepdash' | 'validator'
  table_name TEXT,
  details JSONB,
  CONSTRAINT chk_kind CHECK (kind IN ('auto_fix','qa_drop','chat_autofix','reject'))
);

CREATE INDEX IF NOT EXISTS idx_svev_slug_ts
  ON dash.dash_sql_validator_events(project_slug, ts DESC);

CREATE INDEX IF NOT EXISTS idx_svev_kind_ts
  ON dash.dash_sql_validator_events(kind, ts DESC);
