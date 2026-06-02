-- Migration 063: Per-project consumer-mode embed
-- Adds response_style/access_mode/test_ip_allowlist/max_reply_chars columns and
-- a unique partial index that enforces at most one auto-provisioned project-level
-- embed per project (where agent_id IS NULL).

ALTER TABLE public.dash_agent_embeds
  ADD COLUMN IF NOT EXISTS response_style    TEXT   DEFAULT 'consumer',  -- consumer|developer
  ADD COLUMN IF NOT EXISTS access_mode       TEXT   DEFAULT 'public',    -- public|signed|dashboard|ip_allowlist
  ADD COLUMN IF NOT EXISTS test_ip_allowlist TEXT[] DEFAULT '{}',
  ADD COLUMN IF NOT EXISTS max_reply_chars   INT    DEFAULT 600;

-- One auto-provisioned project-level embed per project (agent_id NULL).
-- Per-agent auto embeds (agent_id IS NOT NULL) are governed by uq_embeds_auto_agent
-- from migration 062.
CREATE UNIQUE INDEX IF NOT EXISTS uq_embeds_auto_project
  ON public.dash_agent_embeds(project_slug)
  WHERE auto_provisioned = TRUE AND agent_id IS NULL;
