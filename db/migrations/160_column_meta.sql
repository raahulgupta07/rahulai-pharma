-- 154_column_meta.sql — LLM column-description enrichment store
-- Idempotent. Lives in public schema (platform metadata).

CREATE TABLE IF NOT EXISTS public.dash_column_meta (
  project_slug TEXT NOT NULL,
  table_name   TEXT NOT NULL,
  column_name  TEXT NOT NULL,
  semantic_type TEXT,
  cardinality_class TEXT,
  description TEXT,
  samples JSONB DEFAULT '[]'::jsonb,
  quality JSONB DEFAULT '{}'::jsonb,
  relationships JSONB DEFAULT '[]'::jsonb,
  glossary_term TEXT,
  glossary_link TEXT,
  suggested_questions JSONB DEFAULT '[]'::jsonb,
  owner TEXT,
  reviewed_at TIMESTAMPTZ,
  provenance JSONB DEFAULT '{}'::jsonb,
  generation_model TEXT,
  generated_at TIMESTAMPTZ DEFAULT now(),
  PRIMARY KEY (project_slug, table_name, column_name)
);

CREATE INDEX IF NOT EXISTS idx_dash_column_meta_project
  ON public.dash_column_meta(project_slug);

CREATE INDEX IF NOT EXISTS idx_dash_column_meta_table
  ON public.dash_column_meta(project_slug, table_name);
