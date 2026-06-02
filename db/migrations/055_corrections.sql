-- Correction-learning loop: capture user edits to agent output, extract durable rules.

CREATE TABLE IF NOT EXISTS dash.dash_corrections (
  id BIGSERIAL PRIMARY KEY,
  project_slug TEXT,
  run_id TEXT,
  agent_name TEXT,
  original_output TEXT,
  edited_output TEXT,
  diff_summary TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_by TEXT
);

CREATE TABLE IF NOT EXISTS dash.dash_correction_rules (
  id BIGSERIAL PRIMARY KEY,
  project_slug TEXT,
  scope TEXT NOT NULL DEFAULT 'project',          -- 'project' | 'agent' | 'skill'
  scope_target TEXT,                              -- agent name / skill name / NULL for project
  rule_text TEXT NOT NULL,
  source_correction_id BIGINT REFERENCES dash.dash_corrections(id) ON DELETE SET NULL,
  active BOOLEAN NOT NULL DEFAULT true,
  hit_count INTEGER NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_corr_rules_lookup
  ON dash.dash_correction_rules(project_slug, scope, scope_target, active);

CREATE INDEX IF NOT EXISTS idx_corrections_recent
  ON dash.dash_corrections(project_slug, created_at DESC);
