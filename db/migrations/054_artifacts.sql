-- Dash-OS — Artifact gallery
-- Extends dash_generated_files (042) with kind, thumbnail, deleted_at.
-- One artifact per (project, run_id, file). Storage on disk under storage_path
-- (filesystem strategy inherited from 042). Thumbnails (PNG, <=240px) inlined
-- as BYTEA for fast grid render.

ALTER TABLE dash.dash_generated_files
  ADD COLUMN IF NOT EXISTS kind TEXT;          -- csv|png|svg|json|pdf|md|pptx|xlsx|docx|html|other

ALTER TABLE dash.dash_generated_files
  ADD COLUMN IF NOT EXISTS thumbnail BYTEA;    -- inline PNG <=240px, NULL for non-image

ALTER TABLE dash.dash_generated_files
  ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;  -- soft delete; NULL = live

-- Backfill kind from file_type for existing rows
UPDATE dash.dash_generated_files
   SET kind = file_type
 WHERE kind IS NULL AND file_type IS NOT NULL;

-- Gallery index (project + run_id, recent first, live only)
CREATE INDEX IF NOT EXISTS idx_genfile_artifact_gallery
  ON dash.dash_generated_files(project_slug, run_id, created_at DESC)
  WHERE deleted_at IS NULL;

-- Kind filter index
CREATE INDEX IF NOT EXISTS idx_genfile_kind
  ON dash.dash_generated_files(project_slug, kind, created_at DESC)
  WHERE deleted_at IS NULL;
