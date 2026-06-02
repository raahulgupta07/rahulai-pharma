-- Migration 148: dash_decisions table for saved recommendations / decision diary.
-- Idempotent — safe if migration 091 already created the base table.
-- Adds extended columns (session_id, decision_text, evidence, owner_user_id, due_at,
-- source_message_id, updated_at) and a richer status vocabulary
-- (pending|in_progress|done|cancelled). The legacy 091 schema (action/owner/effort/risk/
-- chat_msg_id/source_excerpt) is preserved — both can coexist.

CREATE TABLE IF NOT EXISTS public.dash_decisions (
    id              BIGSERIAL PRIMARY KEY,
    project_slug    TEXT        NOT NULL,
    user_id         INTEGER     NOT NULL DEFAULT 0,
    session_id      TEXT,
    decision_text   TEXT        NOT NULL DEFAULT '',
    evidence        JSONB       NOT NULL DEFAULT '{}'::jsonb,
    owner_user_id   INTEGER,
    due_at          TIMESTAMPTZ,
    status          TEXT        NOT NULL DEFAULT 'pending',
    source_message_id TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Idempotent ALTERs for the case where 091 created the table with the legacy schema.
ALTER TABLE public.dash_decisions ADD COLUMN IF NOT EXISTS session_id        TEXT;
ALTER TABLE public.dash_decisions ADD COLUMN IF NOT EXISTS decision_text     TEXT        NOT NULL DEFAULT '';
ALTER TABLE public.dash_decisions ADD COLUMN IF NOT EXISTS evidence          JSONB       NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE public.dash_decisions ADD COLUMN IF NOT EXISTS owner_user_id     INTEGER;
ALTER TABLE public.dash_decisions ADD COLUMN IF NOT EXISTS due_at            TIMESTAMPTZ;
ALTER TABLE public.dash_decisions ADD COLUMN IF NOT EXISTS source_message_id TEXT;
ALTER TABLE public.dash_decisions ADD COLUMN IF NOT EXISTS updated_at        TIMESTAMPTZ NOT NULL DEFAULT now();

-- user_id may have been nullable INTEGER in 091; make sure it has a sane default.
ALTER TABLE public.dash_decisions ALTER COLUMN user_id SET DEFAULT 0;

-- Indexes
CREATE INDEX IF NOT EXISTS idx_dash_decisions_slug_status
    ON public.dash_decisions (project_slug, status);

CREATE INDEX IF NOT EXISTS idx_dash_decisions_owner_open
    ON public.dash_decisions (owner_user_id, status)
    WHERE status IN ('pending', 'in_progress');

CREATE INDEX IF NOT EXISTS idx_dash_decisions_created_desc
    ON public.dash_decisions (created_at DESC);
