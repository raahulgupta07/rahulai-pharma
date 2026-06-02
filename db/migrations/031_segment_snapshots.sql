-- 031_segment_snapshots.sql
-- Auto-Campaign Daemon (Tier 4): per-cycle snapshots of RFM + churn
-- distributions per project. Daemon compares the latest snapshot against
-- the previous cycle to detect significant segment shifts and auto-draft
-- campaigns.
--
-- Also adds a `metadata` JSONB column to `dash_campaigns` so the daemon
-- can stash structured reasoning (rule, detected_change, suggested_*,
-- expected_revenue_lift) alongside the campaign row itself.

CREATE TABLE IF NOT EXISTS dash.dash_segment_snapshots (
    id BIGSERIAL PRIMARY KEY,
    project_slug TEXT NOT NULL,
    captured_at TIMESTAMPTZ DEFAULT now(),
    rfm_distribution JSONB NOT NULL,    -- {Champions: 142, "At Risk": 50, ...}
    churn_distribution JSONB NOT NULL,  -- {active: 800, cooling: 120, at_risk: 60, churned: 20}
    total_customers INT,
    metadata JSONB
);

CREATE INDEX IF NOT EXISTS idx_segment_snapshots_slug_time
    ON dash.dash_segment_snapshots(project_slug, captured_at DESC);

-- Reasoning blob lives on the campaign itself so the UI can render it
-- without a join. JSONB so we can extend without further migrations.
ALTER TABLE IF EXISTS dash.dash_campaigns
    ADD COLUMN IF NOT EXISTS metadata JSONB DEFAULT '{}'::jsonb;

CREATE INDEX IF NOT EXISTS idx_campaigns_auto_rule
    ON dash.dash_campaigns ((metadata->>'rule'))
    WHERE type = 'auto';
