-- Migration 163: dash_llm_model_catalog — OpenRouter model catalog snapshot for searchable browse.
-- Synced daily by dash/cron/llm_catalog_sync.py via OpenRouter /api/v1/models.

CREATE TABLE IF NOT EXISTS dash.dash_llm_model_catalog (
  id              TEXT PRIMARY KEY,
  name            TEXT NOT NULL,
  provider        TEXT NOT NULL,
  description     TEXT,
  context_length  INT,
  pricing_prompt  NUMERIC,
  pricing_completion NUMERIC,
  modalities      JSONB DEFAULT '[]'::jsonb,
  supported_params JSONB DEFAULT '[]'::jsonb,
  top_provider    JSONB,
  is_free         BOOLEAN GENERATED ALWAYS AS (pricing_prompt = 0) STORED,
  raw             JSONB,
  synced_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_llm_catalog_search
  ON dash.dash_llm_model_catalog
  USING GIN (to_tsvector('simple', id || ' ' || COALESCE(name,'') || ' ' || provider));
CREATE INDEX IF NOT EXISTS idx_llm_catalog_provider ON dash.dash_llm_model_catalog (provider);
CREATE INDEX IF NOT EXISTS idx_llm_catalog_free ON dash.dash_llm_model_catalog (is_free) WHERE is_free;
CREATE INDEX IF NOT EXISTS idx_llm_catalog_ctx ON dash.dash_llm_model_catalog (context_length DESC NULLS LAST);
