-- Dash-OS Phase 5 — Comm surfaces: Slack workspaces, email accounts, voice numbers, threads

CREATE TABLE IF NOT EXISTS dash.dash_slack_workspaces (
  id TEXT PRIMARY KEY,                  -- 'sw_<8hex>'
  team_id TEXT NOT NULL UNIQUE,         -- Slack workspace team_id
  team_name TEXT,
  default_project_slug TEXT,            -- which Dash project routes by default
  bot_token TEXT NOT NULL,              -- xoxb-... (encrypted at rest in prod)
  bot_user_id TEXT,
  signing_secret TEXT,
  enabled BOOLEAN NOT NULL DEFAULT true,
  installed_by INTEGER,
  installed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS dash.dash_slack_channel_routes (
  id BIGSERIAL PRIMARY KEY,
  workspace_id TEXT NOT NULL REFERENCES dash.dash_slack_workspaces(id) ON DELETE CASCADE,
  channel_id TEXT NOT NULL,             -- C... or D...
  project_slug TEXT NOT NULL,
  enabled BOOLEAN NOT NULL DEFAULT true,
  UNIQUE(workspace_id, channel_id)
);

CREATE TABLE IF NOT EXISTS dash.dash_email_accounts (
  id TEXT PRIMARY KEY,                  -- 'em_<8hex>'
  name TEXT NOT NULL,
  inbound_kind TEXT NOT NULL,           -- 'imap' | 'ses_webhook'
  imap_host TEXT,
  imap_port INTEGER,
  imap_user TEXT,
  imap_pass TEXT,                       -- encrypted at rest in prod
  smtp_host TEXT,
  smtp_port INTEGER,
  smtp_user TEXT,
  smtp_pass TEXT,
  default_project_slug TEXT,
  subject_prefix_pattern TEXT DEFAULT '^\\[([a-z0-9_-]+)\\]',  -- regex captures project_slug from subject
  enabled BOOLEAN NOT NULL DEFAULT true,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS dash.dash_voice_numbers (
  id TEXT PRIMARY KEY,                  -- 'vn_<8hex>'
  phone_number TEXT NOT NULL UNIQUE,
  provider TEXT NOT NULL DEFAULT 'twilio',
  account_sid TEXT,
  auth_token TEXT,
  default_project_slug TEXT,
  tts_voice TEXT DEFAULT 'Rachel',
  enabled BOOLEAN NOT NULL DEFAULT true,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS dash.dash_channel_threads (
  id TEXT PRIMARY KEY,                  -- 'thd_<8hex>'
  channel_kind TEXT NOT NULL,           -- 'slack' | 'email' | 'voice'
  external_id TEXT NOT NULL,            -- slack thread_ts / email Message-ID / call SID
  workspace_id TEXT,                    -- for slack
  channel_id TEXT,                      -- slack channel or email account or voice number
  project_slug TEXT NOT NULL,
  dash_session_id TEXT,                 -- maps to agno_sessions.session_id
  external_user TEXT,                   -- slack user_id / email from / phone number
  subject TEXT,
  status TEXT NOT NULL DEFAULT 'open',  -- open|closed
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_message_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(channel_kind, external_id)
);

CREATE TABLE IF NOT EXISTS dash.dash_channel_messages (
  id BIGSERIAL PRIMARY KEY,
  thread_id TEXT NOT NULL REFERENCES dash.dash_channel_threads(id) ON DELETE CASCADE,
  direction TEXT NOT NULL,              -- 'inbound' | 'outbound'
  external_msg_id TEXT,
  author TEXT,
  body TEXT,
  attachments JSONB,
  agent_response_excerpt TEXT,
  latency_ms INTEGER,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_thread_recent ON dash.dash_channel_threads(last_message_at DESC);
CREATE INDEX IF NOT EXISTS idx_thread_project ON dash.dash_channel_threads(project_slug);
CREATE INDEX IF NOT EXISTS idx_msg_thread ON dash.dash_channel_messages(thread_id, created_at);
CREATE INDEX IF NOT EXISTS idx_slack_route ON dash.dash_slack_channel_routes(workspace_id, channel_id);
