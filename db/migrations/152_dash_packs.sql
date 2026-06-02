-- 152_dash_packs.sql — Internal pack/extension registry (Build Steal #6)
-- Promotes Phase 6 vertical packs (dash/workflows/verticals/) to first-class
-- DB objects so they can be browsed, installed per-project, and audited.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS dash.dash_packs (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT UNIQUE NOT NULL,
    version     TEXT,
    manifest    JSONB NOT NULL DEFAULT '{}'::jsonb,
    author      TEXT,
    source_path TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_dash_packs_name ON dash.dash_packs (name);

CREATE TABLE IF NOT EXISTS dash.dash_pack_installs (
    pack_id      UUID NOT NULL REFERENCES dash.dash_packs(id) ON DELETE CASCADE,
    project_slug TEXT NOT NULL,
    enabled      BOOLEAN NOT NULL DEFAULT TRUE,
    installed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (pack_id, project_slug)
);

CREATE INDEX IF NOT EXISTS idx_dash_pack_installs_project
    ON dash.dash_pack_installs (project_slug);
CREATE INDEX IF NOT EXISTS idx_dash_pack_installs_enabled
    ON dash.dash_pack_installs (project_slug) WHERE enabled = TRUE;
