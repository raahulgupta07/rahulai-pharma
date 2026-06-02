-- Workflow cron schedules — per-workflow cron entries managed by users.
-- Distinct from dash.dash_agent_schedules (agent chat prompts) and from
-- dash.dash_autonomous_workflows.schedule (template-derived loose schedule).
-- A cron daemon (dash.cron.workflow_scheduler) atomically claims due
-- rows and triggers run_now on the workflow.

CREATE TABLE IF NOT EXISTS dash.dash_workflow_schedules (
  id BIGSERIAL PRIMARY KEY,
  workflow_id BIGINT NOT NULL,
  project_slug TEXT,
  cron TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','paused')),
  next_run_at TIMESTAMPTZ,
  last_run_at TIMESTAMPTZ,
  last_run_id BIGINT,
  owner_user_id INT,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_dash_wf_sched_due
  ON dash.dash_workflow_schedules (status, next_run_at);
CREATE INDEX IF NOT EXISTS idx_dash_wf_sched_wf
  ON dash.dash_workflow_schedules (workflow_id);
