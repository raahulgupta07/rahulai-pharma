CREATE TABLE IF NOT EXISTS dash.training_signals (
  id BIGSERIAL PRIMARY KEY,
  project_slug TEXT NOT NULL,
  chat_id TEXT,
  message_id TEXT,
  question TEXT,
  tables_hit JSONB DEFAULT '[]'::jsonb,
  sql_text TEXT,
  sql_success BOOLEAN,
  sql_error TEXT,
  chart_action TEXT,  -- 'rendered', 'edited', 'rejected', null
  followup_clicked BOOLEAN DEFAULT FALSE,
  agent_used TEXT,
  duration_ms INTEGER,
  created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_training_signals_project ON dash.training_signals(project_slug, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_training_signals_chat ON dash.training_signals(chat_id) WHERE chat_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_training_signals_failed ON dash.training_signals(project_slug, created_at) WHERE sql_success = FALSE;
