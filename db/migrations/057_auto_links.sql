-- B2: deterministic zero-LLM auto-linker for Dash knowledge graph.
-- Entities + entity links extracted via regex + page-role rules.

CREATE TABLE IF NOT EXISTS dash.dash_entities (
  id BIGSERIAL PRIMARY KEY,
  project_slug TEXT,
  kind TEXT NOT NULL,                       -- person | company | concept | project | tag | url
  name TEXT NOT NULL,
  name_normalized TEXT NOT NULL,
  aliases TEXT[] DEFAULT ARRAY[]::TEXT[],
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT uq_dash_entities_proj_kind_norm UNIQUE (project_slug, kind, name_normalized)
);

CREATE INDEX IF NOT EXISTS idx_dash_entities_lookup
  ON dash.dash_entities (project_slug, kind, name_normalized);

CREATE INDEX IF NOT EXISTS idx_dash_entities_name
  ON dash.dash_entities (project_slug, name_normalized);

CREATE TABLE IF NOT EXISTS dash.dash_entity_links (
  id BIGSERIAL PRIMARY KEY,
  project_slug TEXT,
  src_entity_id BIGINT NOT NULL REFERENCES dash.dash_entities(id) ON DELETE CASCADE,
  rel TEXT NOT NULL,
  dst_entity_id BIGINT NOT NULL REFERENCES dash.dash_entities(id) ON DELETE CASCADE,
  source_ref TEXT,
  confidence REAL NOT NULL DEFAULT 1.0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT uq_dash_entity_links UNIQUE (project_slug, src_entity_id, rel, dst_entity_id)
);

CREATE INDEX IF NOT EXISTS idx_dash_entity_links_src
  ON dash.dash_entity_links (project_slug, src_entity_id, rel);

CREATE INDEX IF NOT EXISTS idx_dash_entity_links_dst
  ON dash.dash_entity_links (project_slug, dst_entity_id, rel);
