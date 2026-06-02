-- Training Pipeline V2 — per-step status + fingerprint cache backbone.
CREATE TABLE IF NOT EXISTS public.dash_training_steps (
  id            BIGSERIAL PRIMARY KEY,
  run_id        BIGINT,
  project_slug  TEXT   NOT NULL,
  step_no       INT,
  name          TEXT   NOT NULL,
  scope         TEXT   NOT NULL DEFAULT 'project',
  status        TEXT   NOT NULL DEFAULT 'queued',
  fp            TEXT,
  output_ref    TEXT,
  elapsed_ms    INT,
  error         TEXT,
  started_at    TIMESTAMPTZ,
  finished_at   TIMESTAMPTZ,
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_training_steps_cache
  ON public.dash_training_steps (project_slug, name, scope);

CREATE INDEX IF NOT EXISTS idx_training_steps_run
  ON public.dash_training_steps (run_id);

CREATE INDEX IF NOT EXISTS idx_training_steps_lookup
  ON public.dash_training_steps (project_slug, name, scope, status);
