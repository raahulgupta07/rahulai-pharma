-- 035_automl_staging.sql
-- Staging table for AutoML uploaded files (CSV/Excel/Parquet) before training.
-- Populated by POST /api/automl/templates/{id}/upload, consumed by auto-config + runner.

CREATE TABLE IF NOT EXISTS dash.dash_automl_staging (
  id BIGSERIAL PRIMARY KEY,
  project_slug TEXT,
  user_id INT,
  filename TEXT NOT NULL,
  file_path TEXT NOT NULL,
  format TEXT NOT NULL,
  n_rows INT,
  schema_columns JSONB DEFAULT '[]'::jsonb,
  sample_rows JSONB DEFAULT '[]'::jsonb,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_staging_created
  ON dash.dash_automl_staging(created_at);
