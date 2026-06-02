-- 115_connections.sql — dash_connections + dash_connection_audit (idempotent)
-- Frozen contract §5.

CREATE TABLE IF NOT EXISTS dash.dash_connections (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT UNIQUE NOT NULL,
  connector_type TEXT NOT NULL,
  config JSONB NOT NULL DEFAULT '{}'::jsonb,
  credentials TEXT NOT NULL,
  owner_user_id INT,
  enabled BOOLEAN NOT NULL DEFAULT true,
  allow_all_users BOOLEAN NOT NULL DEFAULT false,
  users_allowed JSONB NOT NULL DEFAULT '[]'::jsonb,
  ldap_groups_allowed JSONB NOT NULL DEFAULT '[]'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_dash_connections_type ON dash.dash_connections(connector_type);

CREATE TABLE IF NOT EXISTS dash.dash_connection_audit (
  id BIGSERIAL PRIMARY KEY,
  connection_id UUID REFERENCES dash.dash_connections(id) ON DELETE CASCADE,
  user_id INT,
  action TEXT NOT NULL,
  sql_text TEXT,
  row_count INT,
  duration_ms INT,
  error TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_dash_connection_audit_conn ON dash.dash_connection_audit(connection_id, created_at DESC);
