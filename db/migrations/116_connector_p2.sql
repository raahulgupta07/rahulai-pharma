-- Connector System Phase 2 schema additions
-- Per-connection quotas and secret rotation tracking

ALTER TABLE dash.dash_connections
  ADD COLUMN IF NOT EXISTS query_limit_per_day INT DEFAULT 1000,
  ADD COLUMN IF NOT EXISTS max_bytes_per_query BIGINT,        -- BigQuery dry-run gate
  ADD COLUMN IF NOT EXISTS secret_rotated_at TIMESTAMPTZ DEFAULT now(),
  ADD COLUMN IF NOT EXISTS secret_rotation_alert_days INT DEFAULT 90,
  ADD COLUMN IF NOT EXISTS last_rotation_warning_at TIMESTAMPTZ;

-- PowerBI OBO refresh tokens per user per connection
CREATE TABLE IF NOT EXISTS dash.dash_connection_user_tokens (
  connection_id UUID REFERENCES dash.dash_connections(id) ON DELETE CASCADE,
  user_id INT NOT NULL,
  refresh_token TEXT NOT NULL,            -- Fernet-encrypted
  access_token TEXT,                       -- Fernet-encrypted, cached
  expires_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now(),
  PRIMARY KEY (connection_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_conn_user_tokens_expires
  ON dash.dash_connection_user_tokens(expires_at);
