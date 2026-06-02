-- Phase 6: RLS audit log — every rewrite + every block.
CREATE TABLE IF NOT EXISTS dash.dash_rls_audit (
    id BIGSERIAL PRIMARY KEY,
    project_slug TEXT NOT NULL,
    user_attrs JSONB,
    external_user TEXT,
    embed_id TEXT,
    original_sql TEXT NOT NULL,
    rewritten_sql TEXT,
    mode TEXT,
    blocked BOOL DEFAULT false,
    block_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_rls_audit_project_ts
    ON dash.dash_rls_audit(project_slug, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_rls_audit_blocked
    ON dash.dash_rls_audit(blocked, created_at DESC) WHERE blocked = true;

COMMENT ON TABLE dash.dash_rls_audit IS
    'Per-call audit of RLS rewrites/blocks. Sampled 1-in-N for non-blocked rewrites to control volume.';
