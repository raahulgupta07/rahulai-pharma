-- Phase 1: Per-project RLS config (per-agent row-level access control)
-- Project owners (not platform admins) configure RLS for their agent.

CREATE TABLE IF NOT EXISTS dash.dash_project_rls_config (
    project_slug TEXT PRIMARY KEY REFERENCES public.dash_projects(slug) ON DELETE CASCADE,
    enabled BOOL NOT NULL DEFAULT false,
    mode TEXT NOT NULL DEFAULT 'advisory'
        CHECK (mode IN ('advisory', 'rewrite', 'pg_rls')),
    user_attr_keys TEXT[] NOT NULL DEFAULT '{}',
    table_filters JSONB NOT NULL DEFAULT '{}'::jsonb,
    default_deny BOOL NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_project_rls_config_enabled
    ON dash.dash_project_rls_config(enabled) WHERE enabled = true;

COMMENT ON TABLE dash.dash_project_rls_config IS
    'Per-agent (per-project) RLS config. mode: advisory=LLM-only, rewrite=SQL injection, pg_rls=Postgres policies.';
COMMENT ON COLUMN dash.dash_project_rls_config.user_attr_keys IS
    'Attribute keys passed in via embed user_attrs (e.g. [store_id, region]).';
COMMENT ON COLUMN dash.dash_project_rls_config.table_filters IS
    'Per-table filter expressions: {"sales": "store_id = :store_id"}. Bind vars match user_attr_keys.';
