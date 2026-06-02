-- Migration 065: RLS Blueprints library — preset bundles of claims+policies
-- for one-click apply to embed RLS config. Two kinds: system (shipped, seeded
-- in code at lifespan startup) and user-saved (created via API).
--
-- Wrapped in pg_advisory_lock(72157425) for auto-runner safety (orthogonal to
-- the global runner lock 72157423; mirrors migrations 062–064 pattern).

SELECT pg_advisory_lock(72157425);

CREATE TABLE IF NOT EXISTS public.dash_embed_rls_blueprints (
    id              BIGSERIAL PRIMARY KEY,
    slug            TEXT UNIQUE NOT NULL,
    name            TEXT NOT NULL,
    industry        TEXT,
    icon            TEXT,
    description     TEXT,
    claims          JSONB NOT NULL DEFAULT '[]'::jsonb,
    policies        JSONB NOT NULL DEFAULT '[]'::jsonb,
    required_tables TEXT[] DEFAULT '{}',
    popularity      INTEGER DEFAULT 0,
    is_system       BOOLEAN DEFAULT FALSE,
    created_by      INTEGER,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_rls_blueprints_industry
    ON public.dash_embed_rls_blueprints(industry);

SELECT pg_advisory_unlock(72157425);
