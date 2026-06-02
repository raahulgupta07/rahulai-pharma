-- 084_tool_utility_scores.sql
-- Creates dash.dash_tool_utility_scores (referenced by app/learning.py
-- os_hub_aggregate skills subview at line 1272). Missing table poisons
-- SQLAlchemy txn and surfaces as 'os_hub: skills total failed'.

CREATE SCHEMA IF NOT EXISTS dash;

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

CREATE INDEX IF NOT EXISTS idx_tool_utility_tool_score
    ON dash.dash_tool_utility_scores (tool_name, score DESC);

CREATE INDEX IF NOT EXISTS idx_tool_utility_project_score
    ON dash.dash_tool_utility_scores (project_slug, score DESC);
