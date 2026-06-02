-- Issue #2 / #14: add bypass_roles JSONB column to dash_project_rls_config
-- Idempotent.

ALTER TABLE IF EXISTS dash_project_rls_config
    ADD COLUMN IF NOT EXISTS bypass_roles JSONB NOT NULL DEFAULT '["admin", "super_admin"]'::jsonb;
