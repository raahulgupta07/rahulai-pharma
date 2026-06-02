-- Migration 170: dash_training_jobs (in-process queue for Option B training)
-- Distinct from deleted dash_ml_jobs infra (Round A pivot).
-- Idempotent: CREATE IF NOT EXISTS everywhere.

CREATE TABLE IF NOT EXISTS public.dash_training_jobs (
    id BIGSERIAL PRIMARY KEY,
    run_id BIGINT,
    project_slug TEXT,
    table_name TEXT,
    job_type TEXT,
    status TEXT DEFAULT 'queued',
    payload JSONB,
    result JSONB,
    error TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_dtj_status_created
    ON public.dash_training_jobs (status, created_at);

CREATE INDEX IF NOT EXISTS idx_dtj_run
    ON public.dash_training_jobs (run_id);

CREATE INDEX IF NOT EXISTS idx_dtj_project
    ON public.dash_training_jobs (project_slug);
