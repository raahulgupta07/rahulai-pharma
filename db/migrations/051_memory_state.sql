-- Dash-OS Phase 7 — Entity memory + agentic state + run context audit

CREATE TABLE IF NOT EXISTS dash.dash_entity_memory (
  id BIGSERIAL PRIMARY KEY,
  project_slug TEXT,
  entity_type TEXT NOT NULL,            -- 'customer' | 'sku' | 'employee' | 'vendor' | 'campaign' | etc
  entity_id TEXT NOT NULL,              -- string ID (UUID/external/PK)
  fact TEXT NOT NULL,
  fact_kind TEXT DEFAULT 'observation', -- 'observation' | 'preference' | 'attribute' | 'event'
  confidence NUMERIC DEFAULT 0.7,       -- 0..1
  source TEXT,                          -- 'agent' | 'user' | 'chat' | 'system'
  source_run_id TEXT,
  embedding VECTOR(1536),               -- nullable; pgvector recall
  metadata JSONB,
  created_by INTEGER,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ,
  archived BOOLEAN NOT NULL DEFAULT false
);

CREATE TABLE IF NOT EXISTS dash.dash_agentic_state (
  id BIGSERIAL PRIMARY KEY,
  session_id TEXT NOT NULL,
  agent_name TEXT NOT NULL,
  key TEXT NOT NULL,
  value JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(session_id, agent_name, key)
);

CREATE TABLE IF NOT EXISTS dash.dash_run_context_audit (
  id BIGSERIAL PRIMARY KEY,
  run_id TEXT NOT NULL,
  project_slug TEXT,
  user_id INTEGER,
  agent_name TEXT,
  scope_id TEXT,
  query_intent TEXT,
  user_attrs JSONB,
  external_user TEXT,
  trigger_kind TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_entmem_entity
  ON dash.dash_entity_memory(project_slug, entity_type, entity_id)
  WHERE archived = false;
CREATE INDEX IF NOT EXISTS idx_entmem_kind
  ON dash.dash_entity_memory(entity_type, fact_kind);
CREATE INDEX IF NOT EXISTS idx_entmem_recent
  ON dash.dash_entity_memory(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agstate_session
  ON dash.dash_agentic_state(session_id, agent_name);
CREATE INDEX IF NOT EXISTS idx_rcaudit_recent
  ON dash.dash_run_context_audit(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_rcaudit_run
  ON dash.dash_run_context_audit(run_id);
