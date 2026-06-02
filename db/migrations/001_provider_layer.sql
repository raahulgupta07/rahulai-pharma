-- Provider layer migration — additive, backwards-compatible.
-- Adds per-source scoping to memory/KG/brain/patterns and provider metadata
-- to dash_data_sources. Existing rows behave as before (NULL = legacy/project-wide).

-- 1. dash_data_sources: provider metadata
ALTER TABLE public.dash_data_sources
  ADD COLUMN IF NOT EXISTS mode TEXT NOT NULL DEFAULT 'sync',
  ADD COLUMN IF NOT EXISTS dialect TEXT,
  ADD COLUMN IF NOT EXISTS provider_class TEXT,
  ADD COLUMN IF NOT EXISTS agent_scope TEXT NOT NULL DEFAULT 'project',
  ADD COLUMN IF NOT EXISTS last_watermark JSONB,
  ADD COLUMN IF NOT EXISTS drift_baseline JSONB,
  ADD COLUMN IF NOT EXISTS last_trained_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS degraded BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS last_error TEXT;

-- back-fill provider_class from source_type on existing rows
UPDATE public.dash_data_sources
SET provider_class = CASE source_type
    WHEN 'postgresql' THEN 'postgres_remote'
    WHEN 'mysql' THEN 'mysql_remote'
    WHEN 'fabric' THEN 'fabric'
    WHEN 'sharepoint' THEN 'sharepoint'
    WHEN 'gdrive' THEN 'gdrive'
    ELSE source_type
  END
WHERE provider_class IS NULL;

-- 2. dash_memories: source-scoped memory
ALTER TABLE public.dash_memories
  ADD COLUMN IF NOT EXISTS source_id INTEGER REFERENCES public.dash_data_sources(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_dash_memories_source ON public.dash_memories(source_id) WHERE source_id IS NOT NULL;

-- 3. dash_query_patterns: source-scoped patterns + dialect
ALTER TABLE public.dash_query_patterns
  ADD COLUMN IF NOT EXISTS source_id INTEGER REFERENCES public.dash_data_sources(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS dialect TEXT;
CREATE INDEX IF NOT EXISTS idx_dash_query_patterns_source ON public.dash_query_patterns(source_id) WHERE source_id IS NOT NULL;

-- 4. dash_knowledge_triples: source URI for cross-source linking
ALTER TABLE public.dash_knowledge_triples
  ADD COLUMN IF NOT EXISTS source_uri TEXT;
CREATE INDEX IF NOT EXISTS idx_dash_kg_source_uri ON public.dash_knowledge_triples(source_uri) WHERE source_uri IS NOT NULL;

-- 5. dash_company_brain: 4th scope axis (source-level)
ALTER TABLE public.dash_company_brain
  ADD COLUMN IF NOT EXISTS source_id INTEGER REFERENCES public.dash_data_sources(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_dash_brain_source ON public.dash_company_brain(source_id) WHERE source_id IS NOT NULL;

-- 6. dash_query_plans, dash_evolved_instructions, dash_relationships
ALTER TABLE public.dash_query_plans
  ADD COLUMN IF NOT EXISTS source_id INTEGER REFERENCES public.dash_data_sources(id) ON DELETE SET NULL;
ALTER TABLE public.dash_evolved_instructions
  ADD COLUMN IF NOT EXISTS source_id INTEGER REFERENCES public.dash_data_sources(id) ON DELETE SET NULL;
ALTER TABLE public.dash_relationships
  ADD COLUMN IF NOT EXISTS source_id INTEGER REFERENCES public.dash_data_sources(id) ON DELETE SET NULL;

-- 7. dash_user_preferences: per-source preference dict (already JSONB; nothing to alter, just doc)
-- Convention: prefs JSONB now uses key 'by_source': { source_id: {...prefs...} } in addition to top-level project prefs.

-- 8. New table: dash_source_training_runs (mirrors dash_training_runs but scoped to one source)
CREATE TABLE IF NOT EXISTS public.dash_source_training_runs (
  id SERIAL PRIMARY KEY,
  source_id INTEGER NOT NULL REFERENCES public.dash_data_sources(id) ON DELETE CASCADE,
  project_slug TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'running',
  current_step TEXT,
  total_steps INTEGER,
  cost_usd NUMERIC(10,4),
  duration_seconds INTEGER,
  error TEXT,
  started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  completed_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_dash_source_training_source ON public.dash_source_training_runs(source_id);
CREATE INDEX IF NOT EXISTS idx_dash_source_training_started ON public.dash_source_training_runs(started_at DESC);

-- 9. Comment columns for self-documentation
COMMENT ON COLUMN public.dash_data_sources.mode IS 'sync | live | hybrid — how queries reach the source';
COMMENT ON COLUMN public.dash_data_sources.agent_scope IS 'project | analyst_only | researcher_only | shared';
COMMENT ON COLUMN public.dash_memories.source_id IS 'NULL = project-wide; non-NULL = source-scoped memory';
COMMENT ON COLUMN public.dash_knowledge_triples.source_uri IS 'e.g. fabric:42:workspace/lakehouse — for cross-source disambiguation';

-- ROLLBACK PLAN (manual):
--   ALTER TABLE ... DROP COLUMN IF EXISTS ...;  for each added column
--   DROP TABLE IF EXISTS public.dash_source_training_runs;
