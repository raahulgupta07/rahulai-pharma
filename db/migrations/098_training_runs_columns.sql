-- 098_training_runs_columns.sql
--
-- Issue #8 — `dash_training_runs.current_step` and `stage_progress` are
-- referenced by Python callers (scope_deriver, learning helpers) but the
-- columns don't actually exist on the table. SELECT/UPDATE statements
-- referencing them silently raise UndefinedColumn errors which are then
-- swallowed by `except Exception: pass` blocks, masking progress reporting.
--
-- Issue #16 — the existing `steps` TEXT column uses an ad-hoc
-- pipe-delimited format `step_name|table_name|index|total` that is
-- fragile to parse on the frontend. Rather than tear it up (still consumed
-- by older callers + frontend fallback parser), add a structured JSONB
-- column `current_progress` that carries the same data in a parseable shape.
--
-- Idempotent — `ADD COLUMN IF NOT EXISTS` keeps the migration safe to
-- re-run. Existing rows get NULL / '{}'::jsonb so callers must continue
-- to tolerate missing values.

ALTER TABLE public.dash_training_runs
  ADD COLUMN IF NOT EXISTS current_step    TEXT,
  ADD COLUMN IF NOT EXISTS stage_progress  INT,
  ADD COLUMN IF NOT EXISTS current_progress JSONB NOT NULL DEFAULT '{}'::jsonb;

-- Cheap lookup index for "show me the active step for project X"
CREATE INDEX IF NOT EXISTS idx_dash_training_runs_status_step
  ON public.dash_training_runs (project_slug, status);
