-- 146_system_status.sql — Track ops state (last backup, last health probe, …)
-- Idempotent. CLAUDE.md rules: use public schema for platform metadata writes.

CREATE TABLE IF NOT EXISTS public.dash_system_status (
    id              SMALLINT PRIMARY KEY DEFAULT 1,
    last_backup_at  TIMESTAMPTZ,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT dash_system_status_singleton CHECK (id = 1)
);

ALTER TABLE public.dash_system_status
    ADD COLUMN IF NOT EXISTS last_backup_at TIMESTAMPTZ;

-- Seed singleton row so UPDATE always hits.
INSERT INTO public.dash_system_status (id) VALUES (1)
ON CONFLICT (id) DO NOTHING;
