-- Migration 162: dash_llm_keys — Fernet-encrypted OpenRouter API key registry.
-- Reuses dash/connectors/crypto.py Fernet (CONNECTION_ENCRYPTION_KEY env).
-- Pool refreshes keys every 60s via dash/llm_client.py OpenRouterPool.

CREATE TABLE IF NOT EXISTS dash.dash_llm_keys (
  id              SERIAL PRIMARY KEY,
  key_label       TEXT NOT NULL,                       -- "primary" / "backup-2" / etc
  encrypted_key   TEXT NOT NULL,                       -- Fernet-encrypted full key
  key_suffix      TEXT NOT NULL,                       -- last 6 chars for UI display
  provider        TEXT NOT NULL DEFAULT 'openrouter',
  enabled         BOOLEAN NOT NULL DEFAULT TRUE,
  created_by      INT,                                 -- dash_users.id (FK soft, no constraint)
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_used_at    TIMESTAMPTZ,
  notes           TEXT
);

CREATE INDEX IF NOT EXISTS idx_dash_llm_keys_enabled
  ON dash.dash_llm_keys (provider, enabled) WHERE enabled = TRUE;
