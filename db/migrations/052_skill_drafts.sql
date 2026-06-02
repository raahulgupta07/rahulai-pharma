-- Dash-OS Phase 10A — Skill drafter: persisted LLM-authored SKILL.md proposals
-- Behind EXPERIMENTAL_AGI=1. Rows created only when flag enabled.

CREATE TABLE IF NOT EXISTS dash.dash_skill_drafts (
  id TEXT PRIMARY KEY,                  -- 'sd_<8hex>'
  project_slug TEXT,
  source_run_id TEXT,
  source_conversation_excerpt TEXT,
  drafted_by_agent TEXT,
  trigger_phrase TEXT,
  iteration INTEGER DEFAULT 1,
  proposed_name TEXT,
  proposed_description TEXT,
  proposed_skill_md TEXT NOT NULL,
  frontmatter JSONB,
  verifier_results JSONB,
  status TEXT NOT NULL DEFAULT 'pending',  -- pending|verifying|verified|approved|rejected|auto_promoted
  rejection_reason TEXT,
  promoted_skill_id TEXT,
  approved_by INTEGER,
  approved_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_sd_status ON dash.dash_skill_drafts(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sd_project ON dash.dash_skill_drafts(project_slug, created_at DESC);
