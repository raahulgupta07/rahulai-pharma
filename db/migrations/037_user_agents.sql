-- 037_user_agents.sql
-- Per-user agent persona + memory events + simulations + audit log.
-- Requires pgvector extension (vector type) — already enabled by 028_vectors.sql.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS user_agents (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL UNIQUE,
  persona_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  zep_session_id TEXT,
  graph_id TEXT,
  state TEXT NOT NULL DEFAULT 'building',  -- building | ready | training | archived | error
  enabled BOOLEAN NOT NULL DEFAULT false,
  last_sync TIMESTAMPTZ,
  version INT NOT NULL DEFAULT 1,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_user_agents_user ON user_agents(user_id);
CREATE INDEX IF NOT EXISTS idx_user_agents_state ON user_agents(state);

CREATE TABLE IF NOT EXISTS agent_memory_events (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  agent_id UUID NOT NULL REFERENCES user_agents(id) ON DELETE CASCADE,
  event_type TEXT NOT NULL,  -- query | decision | feedback | action | observation
  payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  embedding vector(1536),
  ts TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_agent_mem_agent_ts ON agent_memory_events(agent_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_agent_mem_type ON agent_memory_events(agent_id, event_type);

CREATE TABLE IF NOT EXISTS agent_simulations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  agent_id UUID REFERENCES user_agents(id) ON DELETE SET NULL,
  user_id UUID NOT NULL,
  scenario TEXT NOT NULL,
  horizon TEXT,
  seed_tables JSONB,
  status TEXT NOT NULL DEFAULT 'queued',  -- queued | running | done | failed
  progress INT NOT NULL DEFAULT 0,
  result_json JSONB,
  report_md TEXT,
  error TEXT,
  started_at TIMESTAMPTZ,
  finished_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_agent_sim_user ON agent_simulations(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_sim_status ON agent_simulations(status);

CREATE TABLE IF NOT EXISTS agent_audit_log (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  agent_id UUID,
  user_id UUID,
  action TEXT NOT NULL,
  detail JSONB,
  ts TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_agent_audit_user_ts ON agent_audit_log(user_id, ts DESC);

-- updated_at trigger: no shared helper exists in earlier migrations
-- (001_provider_layer.sql and 024_user_scopes.sql have no trigger pattern),
-- so define a local function and attach to user_agents.
CREATE OR REPLACE FUNCTION user_agents_set_updated_at() RETURNS trigger AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_user_agents_updated_at ON user_agents;
CREATE TRIGGER trg_user_agents_updated_at
  BEFORE UPDATE ON user_agents
  FOR EACH ROW
  EXECUTE FUNCTION user_agents_set_updated_at();
