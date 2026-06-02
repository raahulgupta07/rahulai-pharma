-- Migration 153: dash_canvas — Obsidian-style free-form canvas boards
-- Idempotent. Stored in `dash` schema.

CREATE SCHEMA IF NOT EXISTS dash;

CREATE TABLE IF NOT EXISTS dash.dash_canvas (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_slug TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT 'Untitled canvas',
    board JSONB NOT NULL DEFAULT '{"cards": []}'::jsonb,
    created_by TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_dash_canvas_project_updated
    ON dash.dash_canvas (project_slug, updated_at DESC);
