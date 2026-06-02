-- 019_agent_embeds.sql — embeddable agent widget configs + sessions.
-- Idempotent.

CREATE TABLE IF NOT EXISTS public.dash_agent_embeds (
    id                  SERIAL PRIMARY KEY,
    embed_id            TEXT UNIQUE NOT NULL,         -- 'emb_4xH9k2…' public
    project_slug        TEXT NOT NULL,
    public_key          TEXT UNIQUE NOT NULL,         -- 'pub_xxx' safe in browser
    secret_key_hash     TEXT NOT NULL,                -- store hash, never plaintext
    name                TEXT,
    allowed_origins     TEXT[] NOT NULL DEFAULT '{}',
    user_id_required    BOOLEAN DEFAULT FALSE,
    user_id_signed      BOOLEAN DEFAULT TRUE,
    auth_mode           TEXT DEFAULT 'hmac',          -- 'public'|'hmac'|'jwt'
    jwt_jwks_url        TEXT,
    rate_limit_per_min  INTEGER DEFAULT 30,
    feature_config      JSONB,
    enabled             BOOLEAN DEFAULT TRUE,
    created_by          INTEGER,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    last_used_at        TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_embeds_project ON public.dash_agent_embeds(project_slug, enabled);

CREATE TABLE IF NOT EXISTS public.dash_embed_sessions (
    id              BIGSERIAL PRIMARY KEY,
    embed_id        TEXT NOT NULL,
    session_token   TEXT UNIQUE NOT NULL,
    external_user   TEXT,                              -- host's user_id
    user_attrs      JSONB,                             -- {store_id, role, ...}
    origin          TEXT,
    ip              TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    expires_at      TIMESTAMPTZ NOT NULL,
    revoked         BOOLEAN DEFAULT FALSE,
    request_count   INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_embed_sess_token ON public.dash_embed_sessions(session_token);
CREATE INDEX IF NOT EXISTS idx_embed_sess_emb ON public.dash_embed_sessions(embed_id, created_at DESC);

COMMENT ON TABLE public.dash_agent_embeds IS 'Embeddable agent widgets — public_key for browser, secret stored hashed';
COMMENT ON TABLE public.dash_embed_sessions IS 'Per-host-user sessions holding short-lived chat tokens';
