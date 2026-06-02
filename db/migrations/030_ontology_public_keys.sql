-- 030_ontology_public_keys.sql — public read API keys for the Ontology Workbench.
-- Idempotent. Mirrors the pattern from 019_agent_embeds.sql.

CREATE TABLE IF NOT EXISTS public.dash_ontology_api_keys (
    id                  BIGSERIAL PRIMARY KEY,
    name                TEXT NOT NULL,
    public_key          TEXT UNIQUE NOT NULL,         -- 'dop_pub_<32hex>'
    secret_key_hash     TEXT NOT NULL,                -- sha256 of secret bearer
    project_slug        TEXT,                         -- NULL = global read across all projects
    scope               JSONB DEFAULT '{}'::jsonb,    -- {types,glossary,links,lineage,...}
    rate_limit_per_min  INTEGER DEFAULT 60,
    status              TEXT DEFAULT 'active',        -- 'active' | 'revoked'
    allowed_origins     TEXT[],                       -- optional CORS origin allowlist
    created_by          BIGINT,
    created_at          TIMESTAMPTZ DEFAULT now(),
    last_used_at        TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_ontology_keys_status
    ON public.dash_ontology_api_keys(status);

CREATE TABLE IF NOT EXISTS public.dash_ontology_api_calls (
    id           BIGSERIAL PRIMARY KEY,
    key_id       BIGINT NOT NULL,
    endpoint     TEXT NOT NULL,
    status_code  INTEGER,
    latency_ms   INTEGER,
    ip           TEXT,
    created_at   TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ontology_calls_key_id_time
    ON public.dash_ontology_api_calls(key_id, created_at DESC);
