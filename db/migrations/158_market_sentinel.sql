-- 158_market_sentinel.sql — Market Sentinel pillar (VentureDesk sprint 2).
-- Outside-world intelligence: market signals + TAM/SAM/SOM + competitors.
-- Project-scoped. Idempotent: re-run safe.

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;

-- ── 1. dash_market_signals ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS dash.dash_market_signals (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_slug    TEXT NOT NULL,
    sector          TEXT,
    geography       TEXT,
    signal_type     TEXT NOT NULL CHECK (signal_type IN
        ('news','filing','patent','hire','funding','product','web_traffic')),
    source_url      TEXT,
    title           TEXT NOT NULL,
    body            TEXT,
    embedding       vector(1536),
    published_at    TIMESTAMPTZ,
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    deal_ids        JSONB NOT NULL DEFAULT '[]'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_market_signals_slug_time
    ON dash.dash_market_signals (project_slug, ingested_at DESC);
CREATE INDEX IF NOT EXISTS idx_market_signals_sector
    ON dash.dash_market_signals (project_slug, sector);
CREATE INDEX IF NOT EXISTS idx_market_signals_type
    ON dash.dash_market_signals (project_slug, signal_type);
-- HNSW vector index (cosine).
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes
        WHERE schemaname='dash' AND indexname='idx_market_signals_embedding_hnsw'
    ) THEN
        EXECUTE 'CREATE INDEX idx_market_signals_embedding_hnsw
                 ON dash.dash_market_signals USING hnsw (embedding vector_cosine_ops)
                 WITH (m = 16, ef_construction = 64)';
    END IF;
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'hnsw index skipped: %', SQLERRM;
END$$;


-- ── 2. dash_tam_sam_estimates ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS dash.dash_tam_sam_estimates (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_slug    TEXT NOT NULL,
    deal_id         UUID NULL,
    sector          TEXT,
    geography       TEXT,
    tam_usd         NUMERIC(22, 2),
    sam_usd         NUMERIC(22, 2),
    som_usd         NUMERIC(22, 2),
    methodology     TEXT NOT NULL DEFAULT 'bottom_up'
        CHECK (methodology IN ('top_down','bottom_up','value_theory','hybrid')),
    assumptions     JSONB NOT NULL DEFAULT '{}'::jsonb,
    computed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    computed_by     TEXT
);
CREATE INDEX IF NOT EXISTS idx_tam_estimates_slug
    ON dash.dash_tam_sam_estimates (project_slug, computed_at DESC);
CREATE INDEX IF NOT EXISTS idx_tam_estimates_deal
    ON dash.dash_tam_sam_estimates (deal_id) WHERE deal_id IS NOT NULL;


-- ── 3. dash_market_competitors ─────────────────────────────────────────
-- NOTE: separate from dash.dash_venture_competitors (deal-scoped competitor
-- snapshot inside venture_desk migration 154). This table is project-scoped,
-- sector-wide market view that aggregates over time.
CREATE TABLE IF NOT EXISTS dash.dash_market_competitors (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_slug    TEXT NOT NULL,
    sector          TEXT,
    name            TEXT NOT NULL,
    geography       TEXT,
    share_pct       NUMERIC(6, 3),
    evidence        JSONB NOT NULL DEFAULT '[]'::jsonb,
    last_updated    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_market_competitors_slug_sector_name
    ON dash.dash_market_competitors (project_slug, sector, name);
CREATE INDEX IF NOT EXISTS idx_market_competitors_slug_sector
    ON dash.dash_market_competitors (project_slug, sector);


-- ── 4. seed Market Sentinel agent template ─────────────────────────────
INSERT INTO dash.dash_custom_agents (
    id, project_slug, name, description, purpose, base_agent,
    agent_md, scoped_skills, scoped_tools, fit_signals,
    source, enabled, is_promoted_global
) VALUES (
    'cag_mktsntnl', NULL, 'Market Sentinel',
    'External market intelligence. Signals, TAM/SAM/SOM sizing, competitor map, trend detection.',
    'Outside-world signals + market sizing + competitor moves',
    'Analyst',
    $MD$---
name: Market Sentinel
base: Analyst
tools: [ingest_market_signal, search_signals, estimate_tam_sam, competitor_map, trend_detect, link_signals_to_deal, summarize_market_for_memo, refresh_competitor_shares]
---
You are Market Sentinel, the external-intelligence agent for VentureDesk.
You serve corporate strategists, venture analysts, and competitive intelligence
teams. Your sole mission: deliver evidence about MARKETS and COMPETITORS so
other agents can make decisions. You never screen deals or set strategy.

You compute three market sizes for any segment:
  TAM = total addressable market (everyone who could buy)
  SAM = serviceable addressable market (who we can reach)
  SOM = serviceable obtainable market (who we will realistically win)
Methods: top_down, bottom_up, value_theory, hybrid. Always attach methodology
and assumptions. If uncertain, say so — never fabricate numbers.

For signals: classify into news, filing, patent, hire, funding, product,
web_traffic. Ingest with source_url + title. Surface emerging clusters via
trend_detect. Link signals to deals so the IC memo can cite them.

For competitors: maintain dash_market_competitors with share_pct and
evidence. Refresh from latest signals when asked.

You DO NOT build financial models (Deal Analyst), set portfolio strategy
(Strategy Architect), track portfolio KPIs (Ops Optimizer), or structure JV
terms (JV Matchmaker). Hand off explicitly when asked.

Always scope to caller's project_slug. Output style: numbers with units
(USD M/B, %), inline source urls as [domain.com], confidence chips.
$MD$,
    '[]'::jsonb,
    '["ingest_market_signal","search_signals","estimate_tam_sam","competitor_map","trend_detect","link_signals_to_deal","summarize_market_for_memo","refresh_competitor_shares"]'::jsonb,
    CAST('{
      "schema_keywords": ["market","tam","sam","som","competitor","sector","share","cagr","funding","signal","trend","news","patent","filing"],
      "entity_types":    ["sector","geography","competitor","signal","market","segment"],
      "domain_phrases":  ["market size","competitive landscape","funding round","share of market","emerging trend","sector outlook","TAM","SAM","SOM"],
      "modality":        {"news": 0.4, "filings": 0.2, "web": 0.2, "patents": 0.1, "other": 0.1}
    }' AS jsonb),
    'builtin', TRUE, TRUE
) ON CONFLICT (project_slug, name) DO UPDATE SET
    description  = EXCLUDED.description,
    agent_md     = EXCLUDED.agent_md,
    scoped_tools = EXCLUDED.scoped_tools,
    fit_signals  = EXCLUDED.fit_signals,
    updated_at   = now();
