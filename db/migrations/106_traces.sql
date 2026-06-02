-- Migration 106: Native observability / tracing spans.
-- Idempotent: IF NOT EXISTS everywhere. Runner records filename in
-- public.dash_migrations after applying (do NOT modify the runner).

-- ── Trace spans (one row per span; root + child spans share trace_id) ─────────
CREATE TABLE IF NOT EXISTS public.dash_traces (
    id            bigserial   PRIMARY KEY,
    trace_id      text        NOT NULL,
    parent_id     text,
    name          text        NOT NULL,   -- e.g. "training.codex_enrich", "chat.analyst.run_sql"
    kind          text        NOT NULL,   -- training | chat | cron | learning | ml | task
    project_slug  text,
    status        text        NOT NULL DEFAULT 'running',  -- running | done | error | skipped
    duration_ms   integer,
    cost_usd      numeric,
    tokens        integer,
    error         text,
    started_at    timestamptz NOT NULL DEFAULT now(),
    finished_at   timestamptz,
    meta          jsonb
);

-- Backfill columns for older installs (idempotent).
ALTER TABLE public.dash_traces ADD COLUMN IF NOT EXISTS trace_id     text;
ALTER TABLE public.dash_traces ADD COLUMN IF NOT EXISTS parent_id    text;
ALTER TABLE public.dash_traces ADD COLUMN IF NOT EXISTS name         text;
ALTER TABLE public.dash_traces ADD COLUMN IF NOT EXISTS kind         text;
ALTER TABLE public.dash_traces ADD COLUMN IF NOT EXISTS project_slug text;
ALTER TABLE public.dash_traces ADD COLUMN IF NOT EXISTS status       text NOT NULL DEFAULT 'running';
ALTER TABLE public.dash_traces ADD COLUMN IF NOT EXISTS duration_ms  integer;
ALTER TABLE public.dash_traces ADD COLUMN IF NOT EXISTS cost_usd     numeric;
ALTER TABLE public.dash_traces ADD COLUMN IF NOT EXISTS tokens       integer;
ALTER TABLE public.dash_traces ADD COLUMN IF NOT EXISTS error        text;
ALTER TABLE public.dash_traces ADD COLUMN IF NOT EXISTS started_at   timestamptz NOT NULL DEFAULT now();
ALTER TABLE public.dash_traces ADD COLUMN IF NOT EXISTS finished_at  timestamptz;
ALTER TABLE public.dash_traces ADD COLUMN IF NOT EXISTS meta         jsonb;

-- ── Indexes ───────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_dash_traces_started_at
    ON public.dash_traces (started_at DESC);
CREATE INDEX IF NOT EXISTS idx_dash_traces_kind
    ON public.dash_traces (kind);
CREATE INDEX IF NOT EXISTS idx_dash_traces_project_slug
    ON public.dash_traces (project_slug);
CREATE INDEX IF NOT EXISTS idx_dash_traces_trace_id
    ON public.dash_traces (trace_id);
CREATE INDEX IF NOT EXISTS idx_dash_traces_kind_started_at
    ON public.dash_traces (kind, started_at DESC);
