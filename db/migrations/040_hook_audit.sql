-- 040_hook_audit.sql
-- Audit log for the agentic pre/post hook framework.
-- Behind EXPERIMENTAL_AGI=1; framework no-ops when disabled.
CREATE TABLE IF NOT EXISTS dash.dash_hook_audit (
  id BIGSERIAL PRIMARY KEY,
  hook_name TEXT NOT NULL,
  hook_kind TEXT NOT NULL,         -- 'pre' | 'post'
  tool_name TEXT NOT NULL,
  agent_name TEXT,
  project_slug TEXT,
  user_id INTEGER,
  run_id TEXT,
  decision TEXT,                   -- 'pass' | 'block' | 'mutate' | 'error'
  reason TEXT,
  latency_ms INTEGER,
  metadata JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_hook_audit_recent
  ON dash.dash_hook_audit(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_hook_audit_block
  ON dash.dash_hook_audit(decision, created_at DESC)
  WHERE decision = 'block';
