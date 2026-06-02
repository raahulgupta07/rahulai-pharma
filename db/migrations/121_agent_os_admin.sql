-- 121_agent_os_admin.sql — Agent-OS admin backend tables (idempotent).
-- All in `dash` schema. Reuses existing dash_autonomous_workflows + user_agents.

-- ── Capabilities (gated feature flags) ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS dash.aos_capabilities (
  id           BIGSERIAL PRIMARY KEY,
  name         TEXT UNIQUE NOT NULL,
  gated        BOOLEAN NOT NULL DEFAULT true,
  default_on   BOOLEAN NOT NULL DEFAULT false,
  description  TEXT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── Quotas (per-agent token/cost/rate limits) ────────────────────────────────
CREATE TABLE IF NOT EXISTS dash.aos_quotas (
  id                 BIGSERIAL PRIMARY KEY,
  agent_id           TEXT UNIQUE NOT NULL,
  tokens_limit       BIGINT,
  calls_per_min      INT,
  dollars_per_day    NUMERIC,
  tokens_used        BIGINT NOT NULL DEFAULT 0,
  dollars_used       NUMERIC NOT NULL DEFAULT 0,
  window_resets_at   TIMESTAMPTZ,
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_aos_quotas_agent ON dash.aos_quotas(agent_id);

-- ── Models (registry of LLM models + cost/perf) ──────────────────────────────
CREATE TABLE IF NOT EXISTS dash.aos_models (
  id              BIGSERIAL PRIMARY KEY,
  name            TEXT UNIQUE NOT NULL,
  role            TEXT,
  p95_ms          INT,
  cost_per_m_in   NUMERIC,
  cost_per_m_out  NUMERIC,
  enabled         BOOLEAN NOT NULL DEFAULT true,
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── Tool registry (governance + health) ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS dash.aos_tool_registry (
  id          BIGSERIAL PRIMARY KEY,
  tool_name   TEXT UNIQUE NOT NULL,
  owner       TEXT,
  enabled     BOOLEAN NOT NULL DEFAULT true,
  calls_24h   INT NOT NULL DEFAULT 0,
  err_pct     NUMERIC NOT NULL DEFAULT 0,
  avg_ms      INT NOT NULL DEFAULT 0,
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── Kill switch (single row, big red button) ─────────────────────────────────
CREATE TABLE IF NOT EXISTS dash.aos_kill_switch (
  id               SERIAL PRIMARY KEY,
  armed            BOOLEAN NOT NULL DEFAULT true,
  last_changed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_changed_by  TEXT
);
INSERT INTO dash.aos_kill_switch (id, armed)
  VALUES (1, true)
  ON CONFLICT (id) DO NOTHING;

-- ── Per-agent overrides (pause/rate-limit individual agents) ─────────────────
CREATE TABLE IF NOT EXISTS dash.aos_agent_overrides (
  id        BIGSERIAL PRIMARY KEY,
  agent_id  TEXT NOT NULL,
  state     TEXT NOT NULL CHECK (state IN ('paused','rate-limited')),
  detail    TEXT,
  set_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  set_by    TEXT
);
CREATE INDEX IF NOT EXISTS idx_aos_overrides_agent ON dash.aos_agent_overrides(agent_id);

-- ── Cost guard (single row, fleet-wide budget) ───────────────────────────────
CREATE TABLE IF NOT EXISTS dash.aos_cost_guard (
  id              SERIAL PRIMARY KEY,
  daily_budget    NUMERIC NOT NULL DEFAULT 200,
  used_today      NUMERIC NOT NULL DEFAULT 0,
  hard_stop_pct   INT NOT NULL DEFAULT 90,
  alert_pct       INT NOT NULL DEFAULT 75,
  alert_email     TEXT,
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO dash.aos_cost_guard (id)
  VALUES (1)
  ON CONFLICT (id) DO NOTHING;

-- ── Seed: 10 capabilities ────────────────────────────────────────────────────
INSERT INTO dash.aos_capabilities (name, gated, default_on, description) VALUES
  ('code.python.exec',  true,  false, 'Execute arbitrary Python code in sandbox'),
  ('web.fetch',         true,  true,  'Fetch external URLs (HTTP GET)'),
  ('db.write',          true,  false, 'Mutating SQL (INSERT/UPDATE/DELETE)'),
  ('db.read',           false, true,  'Read-only SQL queries'),
  ('office_skills',     true,  false, 'Office file edits (xlsx/pptx/docx/pdf)'),
  ('vector_search',     false, true,  'pgvector semantic search'),
  ('sql_exec',          false, true,  'Execute SELECT queries'),
  ('email.send',        true,  false, 'Send outbound email'),
  ('slack.post',        true,  false, 'Post to Slack channels'),
  ('file.upload',       false, true,  'Accept user file uploads')
ON CONFLICT (name) DO NOTHING;

-- ── Seed: 4 models ───────────────────────────────────────────────────────────
INSERT INTO dash.aos_models (name, role, p95_ms, cost_per_m_in, cost_per_m_out, enabled) VALUES
  ('claude-opus-4-7',    'reasoning', 4200, 15.00, 75.00, true),
  ('claude-sonnet-4-6',  'balanced',  2100,  3.00, 15.00, true),
  ('claude-haiku-4-5',   'fast',       850,  0.80,  4.00, true),
  ('gpt-4o-mini',        'cheap',      900,  0.15,  0.60, true)
ON CONFLICT (name) DO NOTHING;

-- ── Seed: 8 tools ────────────────────────────────────────────────────────────
INSERT INTO dash.aos_tool_registry (tool_name, owner, enabled) VALUES
  ('run_sql_query',        'core',     true),
  ('search_all',           'core',     true),
  ('discover_tables',      'core',     true),
  ('auto_visualize',       'core',     true),
  ('web_search',           'research', true),
  ('predict',              'ml',       true),
  ('classify',             'ml',       true),
  ('create_dashboard',     'core',     true)
ON CONFLICT (tool_name) DO NOTHING;
