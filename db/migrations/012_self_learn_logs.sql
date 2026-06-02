-- 012_self_learn_logs.sql
-- Adds on-demand self-learning run telemetry: streaming logs, current step,
-- summary, and counters needed by POST /api/projects/{slug}/learning/run.
-- Idempotent. Safe to re-run.

ALTER TABLE public.dash_self_learning_runs
  ADD COLUMN IF NOT EXISTS logs JSONB DEFAULT '[]'::jsonb;

ALTER TABLE public.dash_self_learning_runs
  ADD COLUMN IF NOT EXISTS current_step TEXT;

ALTER TABLE public.dash_self_learning_runs
  ADD COLUMN IF NOT EXISTS step_index INTEGER DEFAULT 0;

ALTER TABLE public.dash_self_learning_runs
  ADD COLUMN IF NOT EXISTS total_steps INTEGER DEFAULT 8;

ALTER TABLE public.dash_self_learning_runs
  ADD COLUMN IF NOT EXISTS summary TEXT;

ALTER TABLE public.dash_self_learning_runs
  ADD COLUMN IF NOT EXISTS memories_forgotten INTEGER DEFAULT 0;

ALTER TABLE public.dash_self_learning_runs
  ADD COLUMN IF NOT EXISTS focus TEXT;

-- Backfill empty logs for legacy rows so the API never returns NULL.
UPDATE public.dash_self_learning_runs SET logs = '[]'::jsonb WHERE logs IS NULL;
