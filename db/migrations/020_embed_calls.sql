-- 020_embed_calls.sql — per-chat-call audit log for embed usage metrics.
-- Idempotent.

CREATE TABLE IF NOT EXISTS public.dash_embed_calls (
    id              BIGSERIAL PRIMARY KEY,
    embed_id        TEXT NOT NULL,
    session_token   TEXT,
    external_user   TEXT,
    origin          TEXT,
    ip              TEXT,
    message_chars   INTEGER,
    response_chars  INTEGER,
    latency_ms      INTEGER,
    success         BOOLEAN NOT NULL DEFAULT TRUE,
    error           TEXT,
    ts              TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_embed_calls_embed_ts
    ON public.dash_embed_calls(embed_id, ts DESC);

CREATE INDEX IF NOT EXISTS idx_embed_calls_user_ts
    ON public.dash_embed_calls(embed_id, external_user, ts DESC);

COMMENT ON TABLE public.dash_embed_calls IS
    'Per-call audit log for embed chat — used by usage/sessions UI panels';
