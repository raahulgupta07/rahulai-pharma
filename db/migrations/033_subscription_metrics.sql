-- 033_subscription_metrics.sql
-- Tier 4: MRR / ARR Subscription Analytics
-- Stores per-project monthly snapshots of MRR breakdown + retention.
-- Idempotent re-snapshots via UNIQUE(project_slug, period_start).

CREATE TABLE IF NOT EXISTS dash.dash_subscription_snapshots (
  id BIGSERIAL PRIMARY KEY,
  project_slug TEXT NOT NULL,
  captured_at TIMESTAMPTZ DEFAULT now(),
  period_start DATE NOT NULL,        -- e.g. month start
  period_end DATE NOT NULL,
  mrr NUMERIC,
  arr NUMERIC,
  new_mrr NUMERIC,
  expansion_mrr NUMERIC,
  contraction_mrr NUMERIC,
  churn_mrr NUMERIC,
  reactivation_mrr NUMERIC,
  net_new_mrr NUMERIC,
  gross_retention_pct NUMERIC,
  net_retention_pct NUMERIC,
  active_subscribers INT,
  churned_subscribers INT,
  metadata JSONB,
  UNIQUE(project_slug, period_start)
);

CREATE INDEX IF NOT EXISTS idx_sub_snapshots_slug_time
  ON dash.dash_subscription_snapshots(project_slug, period_start DESC);

CREATE INDEX IF NOT EXISTS idx_sub_snapshots_captured
  ON dash.dash_subscription_snapshots(captured_at DESC);
