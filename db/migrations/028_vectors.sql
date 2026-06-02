CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS dash.dash_vectors (
  id           BIGSERIAL PRIMARY KEY,
  project_slug TEXT NOT NULL,
  namespace    TEXT NOT NULL DEFAULT 'docs',
  source_id    TEXT NOT NULL,
  source_table TEXT,
  scope_attrs  JSONB NOT NULL DEFAULT '{}'::jsonb,
  text         TEXT NOT NULL,
  text_hash    TEXT NOT NULL,
  embedding    vector(1536) NOT NULL,
  metadata     JSONB NOT NULL DEFAULT '{}'::jsonb,
  tsv          tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED,
  updated_at   TIMESTAMPTZ DEFAULT NOW(),
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(project_slug, namespace, source_id)
);

-- Idempotent add for re-runs against existing DBs that pre-date created_at.
ALTER TABLE dash.dash_vectors
  ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now();

CREATE INDEX IF NOT EXISTS dash_vec_hnsw ON dash.dash_vectors USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS dash_vec_scope ON dash.dash_vectors (project_slug, namespace);
CREATE INDEX IF NOT EXISTS dash_vec_attrs ON dash.dash_vectors USING gin (scope_attrs);
CREATE INDEX IF NOT EXISTS dash_vec_fts ON dash.dash_vectors USING gin (tsv);
CREATE INDEX IF NOT EXISTS idx_dash_vectors_created_at ON dash.dash_vectors(created_at DESC);

ALTER TABLE dash.dash_vectors ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS vec_scope ON dash.dash_vectors;
CREATE POLICY vec_scope ON dash.dash_vectors
  USING (
    coalesce(current_setting('app.bypass_rls', true), 'false') = 'true'
    OR (
      project_slug = coalesce(current_setting('app.project_slug', true), '')
      AND (
        scope_attrs = '{}'::jsonb
        OR scope_attrs @> coalesce(nullif(current_setting('app.user_attrs', true), '')::jsonb, '{}'::jsonb)
      )
    )
  );

CREATE TABLE IF NOT EXISTS dash.dash_vector_audit (
  id BIGSERIAL PRIMARY KEY,
  project_slug TEXT NOT NULL,
  op TEXT NOT NULL,
  query TEXT,
  scope_attrs JSONB,
  rows_returned INT,
  latency_ms INT,
  ts TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS dash_vec_audit_ts ON dash.dash_vector_audit (project_slug, ts DESC);
