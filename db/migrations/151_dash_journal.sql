-- Migration 151: dash_journal — daily AI-summarized journal per project (Obsidian-style).
-- Idempotent.

CREATE TABLE IF NOT EXISTS public.dash_journal (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_slug    TEXT        NOT NULL,
    journal_date    DATE        NOT NULL,
    stats           JSONB       NOT NULL DEFAULT '{}'::jsonb,
    summary_md      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (project_slug, journal_date)
);

CREATE INDEX IF NOT EXISTS idx_dash_journal_slug_date
    ON public.dash_journal (project_slug, journal_date DESC);
