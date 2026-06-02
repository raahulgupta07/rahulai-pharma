-- Migration 064: Row-level security for project-level embed.
--
-- Adds RLS config columns to dash_agent_embeds + an audit table for tracking
-- denied/redacted accesses. Idempotent. Wrapped in pg_advisory_lock(72157424)
-- for auto-runner safety (orthogonal to the global runner lock 72157423).
--
-- rls_claims JSONB schema (list of claim definitions):
--   [{"key":"store_id","label":"Store","type":"string","required":true,"values":[]}]
--
-- rls_policies JSONB schema (list of field-level rules):
--   [{"table":"inventory","column":"qty","mode":"private","filter":"store_id","bypass_roles":["hq"]}]
-- Modes: private | shared | redacted | hidden
--
-- rls_claim_source: token | hmac | url | header

SELECT pg_advisory_lock(72157424);

ALTER TABLE public.dash_agent_embeds
  ADD COLUMN IF NOT EXISTS rls_enabled      BOOLEAN DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS rls_claims       JSONB   DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS rls_policies     JSONB   DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS rls_claim_source TEXT    DEFAULT 'token';

CREATE TABLE IF NOT EXISTS public.dash_embed_rls_audit (
    id              BIGSERIAL PRIMARY KEY,
    embed_id        TEXT,
    session_token   TEXT,
    claims          JSONB,
    denied_table    TEXT,
    denied_column   TEXT,
    action          TEXT,
    sql_snippet     TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_embed_rls_audit_embed_created
    ON public.dash_embed_rls_audit (embed_id, created_at DESC);

SELECT pg_advisory_unlock(72157424);
