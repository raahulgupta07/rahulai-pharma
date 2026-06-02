-- 093_slide_templates.sql
-- Tenant-uploaded corporate .pptx templates. Profiled into config JSON
-- (layouts + placeholders + theme colors + fonts) for skill-driven
-- presentation builds. Raw bytes kept so we can reuse master slide.
-- Idempotent: CREATE TABLE IF NOT EXISTS + ADD COLUMN IF NOT EXISTS pattern.

CREATE TABLE IF NOT EXISTS dash.dash_slide_templates (
    id            BIGSERIAL PRIMARY KEY,
    project_slug  TEXT,                 -- NULL = global / shared across tenants
    name          TEXT NOT NULL,
    pptx_bytes    BYTEA,                -- original uploaded file (for reuse + master copy)
    config        JSONB NOT NULL,       -- {layouts:[...], colors:[...], fonts:[...]}
    created_by    INT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_slide_templates_project
    ON dash.dash_slide_templates(project_slug);

CREATE INDEX IF NOT EXISTS idx_slide_templates_created_at
    ON dash.dash_slide_templates(created_at DESC);
