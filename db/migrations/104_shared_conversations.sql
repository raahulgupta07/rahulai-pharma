-- Migration 104: Shareable read-only conversation links (FEATURE E)
-- Owned by: app/share_api.py
-- Table: public.dash_shared_conversations
--   One row per public share token. Holds a frozen snapshot of a chat session
--   (messages + optional data lineage) so the public viewer never touches the
--   authenticated conversation store.
-- All DDL is idempotent (CREATE TABLE IF NOT EXISTS, CREATE INDEX IF NOT EXISTS).

-- -------------------------------------------------------------------------
-- 1. dash_shared_conversations
-- -------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.dash_shared_conversations (
    token           TEXT        PRIMARY KEY,
    project_slug    TEXT,
    session_id      TEXT,
    created_by      TEXT,
    created_at      TIMESTAMPTZ DEFAULT now(),
    expires_at      TIMESTAMPTZ,
    revoked         BOOLEAN     DEFAULT FALSE,
    include_lineage BOOLEAN     DEFAULT TRUE,
    snapshot        JSONB
);

-- -------------------------------------------------------------------------
-- 2. Index on expires_at — fast pruning / expiry checks
-- -------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_shared_conversations_expires_at
    ON public.dash_shared_conversations (expires_at);
