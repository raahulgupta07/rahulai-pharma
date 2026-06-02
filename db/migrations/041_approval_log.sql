-- 041_approval_log.sql
-- Cross-cutting generalized approval queue + audit trail.
-- Parallel framework alongside dash/policy/signoff.py — does not migrate
-- existing visibility sign-off (dash_visibility_policy_drafts).
--
-- Behind EXPERIMENTAL_AGI=1 env flag at the decorator layer; tables are
-- always created so migrations are deterministic.

CREATE SCHEMA IF NOT EXISTS dash;

CREATE TABLE IF NOT EXISTS dash.dash_approval_requests (
  id TEXT PRIMARY KEY,                         -- 'apr_<8hex>'
  project_slug TEXT,
  action_type TEXT NOT NULL,                   -- 'brain_delete' | 'rls_apply' | 'dataset_drop' | 'campaign_launch' | etc
  resource_id TEXT,                            -- entity being acted on
  payload JSONB NOT NULL,                      -- full action details (kwargs, fn ref)
  requested_by INTEGER NOT NULL,
  required_approvers INTEGER NOT NULL DEFAULT 1,
  allowed_roles JSONB NOT NULL DEFAULT '["admin"]'::jsonb,
  status TEXT NOT NULL DEFAULT 'pending',      -- pending|approved|rejected|expired|executed|cancelled
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ NOT NULL DEFAULT (now() + INTERVAL '24 hours'),
  resolved_at TIMESTAMPTZ,
  execution_result JSONB
);

CREATE TABLE IF NOT EXISTS dash.dash_approval_signatures (
  id BIGSERIAL PRIMARY KEY,
  request_id TEXT NOT NULL REFERENCES dash.dash_approval_requests(id) ON DELETE CASCADE,
  approver_id INTEGER NOT NULL,
  decision TEXT NOT NULL,                      -- 'approve' | 'reject'
  reason TEXT,
  signed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(request_id, approver_id)
);

CREATE TABLE IF NOT EXISTS dash.dash_approval_audit (
  id BIGSERIAL PRIMARY KEY,
  request_id TEXT,
  event TEXT NOT NULL,                         -- created|signed|approved|rejected|expired|executed|execution_failed|cancelled
  actor_id INTEGER,
  metadata JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_apr_pending
  ON dash.dash_approval_requests(status, expires_at)
  WHERE status='pending';

CREATE INDEX IF NOT EXISTS idx_apr_project
  ON dash.dash_approval_requests(project_slug, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_apr_action
  ON dash.dash_approval_requests(action_type, status);

CREATE INDEX IF NOT EXISTS idx_apr_audit_request
  ON dash.dash_approval_audit(request_id, created_at DESC);
