-- 036_automl_v2.sql
-- AutoML v2: multi-file upload sets, generated reports, follow-up chats.
-- Auto-runner sets search_path = dash, public, ai. All objects in dash schema.

-- Multi-file staging set (one row per upload session)
CREATE TABLE IF NOT EXISTS dash.dash_automl_upload_sets (
  id BIGSERIAL PRIMARY KEY,
  user_id INT NOT NULL,
  template_id TEXT NOT NULL,
  project_slug TEXT,
  status TEXT NOT NULL DEFAULT 'staging',  -- staging|merged|consumed
  files JSONB DEFAULT '[]'::jsonb,         -- [{staging_id, filename, n_rows, columns, join_role}]
  merge_report JSONB DEFAULT '{}'::jsonb,
  eda_findings JSONB DEFAULT '{}'::jsonb,
  domain_interpretation JSONB DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_upload_sets_user
  ON dash.dash_automl_upload_sets(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_upload_sets_status
  ON dash.dash_automl_upload_sets(status);

-- Reports generated per experiment
CREATE TABLE IF NOT EXISTS dash.dash_automl_reports (
  id BIGSERIAL PRIMARY KEY,
  experiment_id BIGINT NOT NULL,
  type TEXT NOT NULL,        -- pdf|pptx|dashboard|email
  file_path TEXT,            -- for pdf/pptx
  dashboard_id INT,          -- if dashboard pinned
  payload JSONB,             -- spec snapshot
  created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_reports_exp
  ON dash.dash_automl_reports(experiment_id);

-- Followup messages (chat scoped to experiment)
CREATE TABLE IF NOT EXISTS dash.dash_automl_followups (
  id BIGSERIAL PRIMARY KEY,
  experiment_id BIGINT NOT NULL,
  user_id INT NOT NULL,
  role TEXT NOT NULL,        -- user|assistant
  content TEXT NOT NULL,
  citations JSONB DEFAULT '[]'::jsonb,
  created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_followups_exp
  ON dash.dash_automl_followups(experiment_id, created_at);
