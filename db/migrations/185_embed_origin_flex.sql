-- 185_embed_origin_flex.sql
-- Wave 1 plug-and-play embedding: flexible origin matching + auto-detect.
--
-- (1) origin_mode per embed key:
--       strict      — only exact entries in allowed_origins (default; legacy behaviour)
--       subdomains  — a listed base origin (https://acme.com) also allows its
--                     subdomains (https://app.acme.com); plus any '*' patterns
--       open        — any origin allowed (insecure; for trusted/internal use)
--     Wildcard patterns in allowed_origins (e.g. https://*.acme.com,
--     http://192.168.*) work in ALL modes — see dash/embed/session.py.
--
-- (2) pending-origins ledger: every blocked Origin is recorded so an admin can
--     one-click Allow it (appends to allowed_origins) or Block it — no code
--     redeploy needed. Fixes the AWS "Origin ... not in allowlist (403)" loop.
--
-- Idempotent.

ALTER TABLE public.dash_agent_embeds
    ADD COLUMN IF NOT EXISTS origin_mode TEXT NOT NULL DEFAULT 'strict';

CREATE TABLE IF NOT EXISTS public.dash_embed_pending_origins (
    id          BIGSERIAL PRIMARY KEY,
    embed_id    TEXT NOT NULL,
    origin      TEXT NOT NULL,
    ip          TEXT,
    country     TEXT,
    attempts    INTEGER NOT NULL DEFAULT 1,
    status      TEXT NOT NULL DEFAULT 'pending',   -- pending | allowed | blocked
    first_seen  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (embed_id, origin)
);

CREATE INDEX IF NOT EXISTS idx_embed_pending_status
    ON public.dash_embed_pending_origins (status, last_seen DESC);

CREATE INDEX IF NOT EXISTS idx_embed_pending_embed
    ON public.dash_embed_pending_origins (embed_id, status);
