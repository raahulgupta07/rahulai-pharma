-- Brain entry versioning + rollback (Ontology Phase D)
-- Snapshots every create/update/delete on dash_company_brain so we can roll back.

CREATE TABLE IF NOT EXISTS public.dash_brain_versions (
    id BIGSERIAL PRIMARY KEY,
    brain_id BIGINT NOT NULL,        -- references dash_company_brain.id (no hard FK; entries can be soft-superseded)
    version INT NOT NULL,            -- 1-based per brain_id
    category TEXT,
    name TEXT,
    definition TEXT,
    project_slug TEXT,
    user_id BIGINT,
    metadata JSONB,
    change_type TEXT NOT NULL,       -- 'create' | 'update' | 'delete' | 'rollback'
    changed_by BIGINT,               -- user_id of editor (NULL for system/agent)
    change_reason TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_brain_versions_brain_id
    ON public.dash_brain_versions (brain_id, version DESC);

CREATE INDEX IF NOT EXISTS idx_brain_versions_changed_by
    ON public.dash_brain_versions (changed_by);

CREATE INDEX IF NOT EXISTS idx_brain_versions_created_at
    ON public.dash_brain_versions (created_at DESC);
