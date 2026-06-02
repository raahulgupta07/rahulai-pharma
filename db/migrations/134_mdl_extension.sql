-- Migration 134: MDL (Modeling Definition Language) extension to dash_metric_definitions
--
-- Inspired by WrenAI's MDL semantic layer. Extends existing metric_definitions
-- with WrenAI-style semantic model fields. NO new tables — additive only.
--
-- Idempotent: ADD COLUMN IF NOT EXISTS throughout.
--
-- Adds 4 fields:
--   model_name        — logical model the metric belongs to (e.g., "customer_calls")
--   raw_table_ref     — raw underlying table (e.g., "crm_jun_2025")
--   virtual_columns   — derived columns w/ expressions [{name, expression, type}]
--   relationships     — joins to other models [{model, on, type}]
--
-- These power:
--   - LLM-facing clean names ("customer_id" not "cust_id")
--   - sqlglot compiler: rewrite semantic SQL → raw SQL at runtime
--   - Portable vertical packs: rebind MDL→raw without rewriting workflows

ALTER TABLE public.dash_metric_definitions
    ADD COLUMN IF NOT EXISTS model_name      TEXT,
    ADD COLUMN IF NOT EXISTS raw_table_ref   TEXT,
    ADD COLUMN IF NOT EXISTS virtual_columns JSONB NOT NULL DEFAULT '[]',
    ADD COLUMN IF NOT EXISTS relationships   JSONB NOT NULL DEFAULT '[]';

-- Index for model-name lookups (semantic layer resolver)
CREATE INDEX IF NOT EXISTS idx_dash_metric_defs_model_name
    ON public.dash_metric_definitions (project_slug, model_name)
    WHERE model_name IS NOT NULL;

-- Comment for next dev
COMMENT ON COLUMN public.dash_metric_definitions.model_name IS
    'WrenAI-style logical model name. Multiple metrics can share a model. NULL = legacy/un-MDL.';
COMMENT ON COLUMN public.dash_metric_definitions.virtual_columns IS
    'Derived columns: [{name, expression, type}]. e.g., {"name":"was_successful","expression":"ot_cd=''successful''","type":"boolean"}';
COMMENT ON COLUMN public.dash_metric_definitions.relationships IS
    'Joins to other models: [{model, on, type}]. type ∈ many_to_one|one_to_many|many_to_many';
