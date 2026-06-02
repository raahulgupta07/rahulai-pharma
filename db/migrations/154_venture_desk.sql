-- 154_venture_desk.sql — VentureDesk: corporate venture / deal analysis tables.
-- Project-scoped per `project_slug`. RLS handled by app layer (existing pattern).
-- Idempotent: re-run safe.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS dash.dash_venture_deals (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_slug  TEXT NOT NULL,
    name          TEXT NOT NULL,
    stage         TEXT,          -- seed / series_a / series_b / late / exit
    sector        TEXT,
    geography     TEXT,
    ask_amount    NUMERIC,
    pre_money     NUMERIC,
    post_money    NUMERIC,
    status        TEXT DEFAULT 'screening',  -- screening / diligence / ic / shortlist / pass / closed
    created_by    INT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_venture_deals_project ON dash.dash_venture_deals (project_slug);
CREATE INDEX IF NOT EXISTS idx_venture_deals_status  ON dash.dash_venture_deals (project_slug, status);

CREATE TABLE IF NOT EXISTS dash.dash_venture_financials (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    deal_id       UUID NOT NULL REFERENCES dash.dash_venture_deals(id) ON DELETE CASCADE,
    year          INT NOT NULL,
    revenue       NUMERIC,
    ebitda        NUMERIC,
    capex         NUMERIC,
    fcf           NUMERIC,
    assumptions   JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (deal_id, year)
);

CREATE INDEX IF NOT EXISTS idx_venture_fin_deal ON dash.dash_venture_financials (deal_id);

CREATE TABLE IF NOT EXISTS dash.dash_venture_scenarios (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    deal_id       UUID NOT NULL REFERENCES dash.dash_venture_deals(id) ON DELETE CASCADE,
    name          TEXT NOT NULL,
    irr           NUMERIC,
    moic          NUMERIC,
    payback_yrs   NUMERIC,
    npv           NUMERIC,
    inputs        JSONB NOT NULL DEFAULT '{}'::jsonb,
    verdict       TEXT,          -- go / hold / pass
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_venture_scen_deal ON dash.dash_venture_scenarios (deal_id);

CREATE TABLE IF NOT EXISTS dash.dash_venture_competitors (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    deal_id       UUID NOT NULL REFERENCES dash.dash_venture_deals(id) ON DELETE CASCADE,
    name          TEXT NOT NULL,
    share_pct     NUMERIC,
    moat          TEXT,
    source        TEXT
);

CREATE INDEX IF NOT EXISTS idx_venture_comp_deal ON dash.dash_venture_competitors (deal_id);

CREATE TABLE IF NOT EXISTS dash.dash_venture_partners (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    deal_id       UUID NOT NULL REFERENCES dash.dash_venture_deals(id) ON DELETE CASCADE,
    name          TEXT NOT NULL,
    role          TEXT,
    equity_pct    NUMERIC,
    fit_score     NUMERIC,
    notes         TEXT
);

CREATE INDEX IF NOT EXISTS idx_venture_part_deal ON dash.dash_venture_partners (deal_id);
