-- 097_skill_runtime_role.sql
-- Add runtime_role column to dash.dash_skills so the skill registry can be
-- honest about what each skill actually does at runtime.
--
--   "pipeline"   = code invokes via _skill_prefix(skill_id), e.g. Deep Dash stages
--   "redirect"   = Leader keyword routing tells user "click X button"
--   "agent_hint" = Leader/Analyst loads skill instructions when keywords match
--   "dev_tool"   = developer-facing; no end-user code path
--   "meta"       = skill-of-skills / orchestration helper
--
-- Default "agent_hint" preserves existing behavior for any skill not explicitly tagged.

ALTER TABLE dash.dash_skills
  ADD COLUMN IF NOT EXISTS runtime_role TEXT NOT NULL DEFAULT 'agent_hint';

CREATE INDEX IF NOT EXISTS idx_dash_skills_runtime_role
  ON dash.dash_skills (runtime_role);
