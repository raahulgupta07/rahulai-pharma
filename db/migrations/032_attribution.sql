-- Multi-Touch Attribution (Tier 4)
-- Migration 032 — touchpoints, conversions, attribution credits

-- Touchpoint events (clicks, opens, visits, ad impressions)
CREATE TABLE IF NOT EXISTS dash.dash_touchpoints (
  id BIGSERIAL PRIMARY KEY,
  project_slug TEXT NOT NULL,
  customer_id TEXT NOT NULL,
  channel TEXT NOT NULL,           -- 'email' | 'sms' | 'ad' | 'organic' | 'direct' | 'social' | 'campaign'
  campaign_id BIGINT,              -- nullable, FK soft-link to dash_campaigns
  event_type TEXT NOT NULL,        -- 'click' | 'open' | 'view' | 'visit' | 'impression'
  event_at TIMESTAMPTZ NOT NULL,
  metadata JSONB DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_tp_slug_cust_time ON dash.dash_touchpoints(project_slug, customer_id, event_at DESC);
CREATE INDEX IF NOT EXISTS idx_tp_campaign ON dash.dash_touchpoints(campaign_id);

-- Conversion records (linked to a transaction)
CREATE TABLE IF NOT EXISTS dash.dash_conversions (
  id BIGSERIAL PRIMARY KEY,
  project_slug TEXT NOT NULL,
  customer_id TEXT NOT NULL,
  transaction_id TEXT,
  revenue NUMERIC,
  converted_at TIMESTAMPTZ NOT NULL,
  metadata JSONB,
  created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_conv_slug_cust_time ON dash.dash_conversions(project_slug, customer_id, converted_at DESC);

-- Computed attribution credits (cached after model run)
CREATE TABLE IF NOT EXISTS dash.dash_attribution_credits (
  id BIGSERIAL PRIMARY KEY,
  project_slug TEXT NOT NULL,
  conversion_id BIGINT NOT NULL,
  touchpoint_id BIGINT NOT NULL,
  model TEXT NOT NULL,             -- 'linear' | 'time_decay' | 'position' | 'markov'
  credit NUMERIC NOT NULL,         -- 0.0–1.0, sum of credits per (conversion,model) = 1.0
  credited_revenue NUMERIC,        -- revenue * credit
  computed_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_attr_slug_conv ON dash.dash_attribution_credits(project_slug, conversion_id);
CREATE INDEX IF NOT EXISTS idx_attr_tp_model ON dash.dash_attribution_credits(touchpoint_id, model);
