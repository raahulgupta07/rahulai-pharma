-- 021_guardrail_audit.sql — log refused queries (auto-scope guardrail).
-- Idempotent.

CREATE TABLE IF NOT EXISTS public.dash_guardrail_audit (
    id              BIGSERIAL PRIMARY KEY,
    project_slug    TEXT NOT NULL,
    user_id         INTEGER,
    embed_id        TEXT,
    external_user   TEXT,
    question        TEXT NOT NULL,
    refusal_reason  TEXT,                  -- 'off_topic' | 'denied_intent' | 'manual_block'
    classifier      TEXT,                  -- 'instructions' | 'preflight'
    matched_topic   TEXT,
    refusal_message TEXT,
    ts              TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_guardrail_audit_proj_ts
    ON public.dash_guardrail_audit(project_slug, ts DESC);

COMMENT ON TABLE public.dash_guardrail_audit IS
    'Auto-scope guardrail: every off-topic refusal logged for review';
