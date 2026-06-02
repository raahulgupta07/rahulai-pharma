-- Campaign Management Lite
-- Migration 026 — campaigns + events + metrics

CREATE TABLE IF NOT EXISTS dash.dash_campaigns (
    id SERIAL PRIMARY KEY,
    project_slug TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    type TEXT NOT NULL DEFAULT 'manual', -- manual|email|sms|push|web
    status TEXT NOT NULL DEFAULT 'draft', -- draft|scheduled|active|paused|completed|cancelled
    target_segment TEXT,                  -- e.g., 'Champions', 'At Risk', 'rfm:555'
    target_filter JSONB DEFAULT '{}'::jsonb,
    audience_size INTEGER DEFAULT 0,
    offer JSONB DEFAULT '{}'::jsonb,
    starts_at TIMESTAMPTZ,
    ends_at TIMESTAMPTZ,
    cost_budget NUMERIC(12,2),
    created_by TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_campaigns_project ON dash.dash_campaigns(project_slug, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_campaigns_status ON dash.dash_campaigns(status);

CREATE TABLE IF NOT EXISTS dash.dash_campaign_events (
    id SERIAL PRIMARY KEY,
    campaign_id INTEGER REFERENCES dash.dash_campaigns(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL, -- created|launched|paused|resumed|completed|metric_recorded|updated|cancelled
    actor TEXT,
    payload JSONB DEFAULT '{}'::jsonb,
    occurred_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_campaign_events_camp ON dash.dash_campaign_events(campaign_id, occurred_at DESC);

CREATE TABLE IF NOT EXISTS dash.dash_campaign_metrics (
    id SERIAL PRIMARY KEY,
    campaign_id INTEGER REFERENCES dash.dash_campaigns(id) ON DELETE CASCADE,
    metric_name TEXT NOT NULL, -- impressions|clicks|conversions|revenue|opt_outs
    value NUMERIC,
    period_start TIMESTAMPTZ,
    period_end TIMESTAMPTZ,
    recorded_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_campaign_metrics_camp ON dash.dash_campaign_metrics(campaign_id, recorded_at DESC);
CREATE INDEX IF NOT EXISTS idx_campaign_metrics_name ON dash.dash_campaign_metrics(campaign_id, metric_name);
