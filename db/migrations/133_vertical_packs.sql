-- Vertical workflow packs — auto-install of workflow templates when a project's
-- schema matches a known vertical (pharmacy, crm_calls, ecommerce, finance, …).
-- Eliminates the "37 broken seeded workflows" class for new projects.
ALTER TABLE dash.dash_autonomous_workflows
  ADD COLUMN IF NOT EXISTS vertical_pack TEXT,
  ADD COLUMN IF NOT EXISTS binding_resolved JSONB;

CREATE TABLE IF NOT EXISTS dash.dash_vertical_pack_history (
  id BIGSERIAL PRIMARY KEY,
  project_slug TEXT NOT NULL,
  pack_name TEXT NOT NULL,
  score NUMERIC,                         -- 0.0–1.0 schema-fit confidence
  workflows_installed INT,
  workflows_skipped INT,
  installed_at TIMESTAMPTZ DEFAULT NOW(),
  installed_by INT
);
CREATE INDEX IF NOT EXISTS idx_vpack_history_slug ON dash.dash_vertical_pack_history (project_slug, installed_at DESC);
