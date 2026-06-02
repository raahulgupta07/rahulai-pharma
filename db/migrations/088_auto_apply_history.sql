-- Auto-apply vertical history — pre-apply snapshot + per-step audit + revert support
CREATE TABLE IF NOT EXISTS dash.dash_auto_apply_history (
  id BIGSERIAL PRIMARY KEY,
  project_slug TEXT NOT NULL,
  vertical TEXT,
  template TEXT,
  confidence REAL,
  detection JSONB,            -- full classifier output
  applied BOOLEAN DEFAULT FALSE,
  snapshot JSONB,             -- pre-apply state for revert
  applied_steps JSONB,        -- per-step result list
  error TEXT,
  applied_by TEXT,            -- 'auto' | user_id
  reverted BOOLEAN DEFAULT FALSE,
  reverted_at TIMESTAMPTZ,
  reverted_by TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_aah_slug
  ON dash.dash_auto_apply_history(project_slug, created_at DESC);
