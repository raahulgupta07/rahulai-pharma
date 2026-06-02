-- Dash B5 — Minions durable job queue (Postgres-only, lease-based, crash-safe)

CREATE TABLE IF NOT EXISTS dash.dash_minions (
  id BIGSERIAL PRIMARY KEY,
  project_slug TEXT,
  kind TEXT NOT NULL,
  payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending','running','done','failed','cancelled')),
  priority INT NOT NULL DEFAULT 5,
  claimed_by TEXT,
  lease_until TIMESTAMPTZ,
  attempts INT NOT NULL DEFAULT 0,
  max_attempts INT NOT NULL DEFAULT 3,
  result JSONB,
  error TEXT,
  scheduled_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  started_at TIMESTAMPTZ,
  finished_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_minions_status_sched
  ON dash.dash_minions(status, scheduled_at);
CREATE INDEX IF NOT EXISTS idx_minions_project_status
  ON dash.dash_minions(project_slug, status);
CREATE INDEX IF NOT EXISTS idx_minions_claim
  ON dash.dash_minions(claimed_by, lease_until);
CREATE INDEX IF NOT EXISTS idx_minions_kind_status
  ON dash.dash_minions(kind, status);
