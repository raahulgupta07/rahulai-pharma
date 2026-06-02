-- 156_ops_optimizer.sql — Ops Optimizer pillar (VentureDesk post-investment).
-- Project-scoped per project_slug (via portco → deal → project chain).
-- Idempotent: re-run safe.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS dash.dash_portco (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_slug TEXT NOT NULL,
    deal_id UUID NOT NULL REFERENCES dash.dash_venture_deals(id) ON DELETE CASCADE,
    legal_name TEXT NOT NULL,
    investment_date DATE NOT NULL,
    ownership_pct NUMERIC(6, 3) NOT NULL,
    board_seat BOOLEAN NOT NULL DEFAULT false,
    stage_at_invest TEXT,
    sector TEXT,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','exited','written_off','watch')),
    fiscal_year_end TEXT NOT NULL DEFAULT 'DEC',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (project_slug, deal_id)
);
CREATE INDEX IF NOT EXISTS idx_portco_slug_status ON dash.dash_portco(project_slug, status);

CREATE TABLE IF NOT EXISTS dash.dash_portco_kpis (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    portco_id UUID NOT NULL REFERENCES dash.dash_portco(id) ON DELETE CASCADE,
    metric_name TEXT NOT NULL,
    metric_category TEXT CHECK (metric_category IN ('revenue','growth','margin','cash','customer','operational','people')),
    unit TEXT NOT NULL,
    period TEXT NOT NULL,
    period_start DATE NOT NULL,
    period_end DATE NOT NULL,
    actual NUMERIC(20, 4),
    plan NUMERIC(20, 4),
    forecast NUMERIC(20, 4),
    variance_pct NUMERIC(8, 3) GENERATED ALWAYS AS (
        CASE WHEN plan IS NULL OR plan = 0 THEN NULL
             ELSE ((actual - plan) / plan) * 100 END
    ) STORED,
    source TEXT NOT NULL DEFAULT 'manual' CHECK (source IN ('manual','api','upload','agent')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (portco_id, metric_name, period)
);
CREATE INDEX IF NOT EXISTS idx_kpis_portco_period ON dash.dash_portco_kpis(portco_id, period_end DESC);

CREATE TABLE IF NOT EXISTS dash.dash_portco_initiatives (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    portco_id UUID NOT NULL REFERENCES dash.dash_portco(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    description TEXT,
    play_type TEXT CHECK (play_type IN ('cost_out','revenue_uplift','margin_expansion','tech_migration','hire','ma_addon','exit_prep')),
    owner TEXT,
    target_metric TEXT,
    target_delta_pct NUMERIC(6, 3),
    target_value_usd NUMERIC(18, 2),
    status TEXT NOT NULL DEFAULT 'proposed' CHECK (status IN ('proposed','approved','in_progress','done','cancelled')),
    start_date DATE,
    due_date DATE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_initiatives_portco ON dash.dash_portco_initiatives(portco_id, status);

CREATE TABLE IF NOT EXISTS dash.dash_portco_board_packs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    portco_id UUID NOT NULL REFERENCES dash.dash_portco(id) ON DELETE CASCADE,
    meeting_date DATE NOT NULL,
    summary TEXT,
    kpi_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    decisions JSONB NOT NULL DEFAULT '[]'::jsonb,
    file_path TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS dash.dash_portco_anomalies (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    portco_id UUID NOT NULL REFERENCES dash.dash_portco(id) ON DELETE CASCADE,
    metric_name TEXT NOT NULL,
    period TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('info','warn','critical')),
    z_score NUMERIC(6, 3),
    explanation TEXT,
    acknowledged BOOLEAN NOT NULL DEFAULT false,
    detected_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_anom_portco_sev ON dash.dash_portco_anomalies(portco_id, severity, acknowledged);
-- Helps idempotent detect_anomalies pre-check (one anomaly per metric+period).
CREATE UNIQUE INDEX IF NOT EXISTS uq_anom_portco_metric_period
    ON dash.dash_portco_anomalies(portco_id, metric_name, period);

-- ── Ops Optimizer agent template (cag_opsoptmzr) ────────────────────────
INSERT INTO dash.dash_custom_agents (
  id, project_slug, name, description, purpose, base_agent,
  agent_md, scoped_skills, scoped_tools, fit_signals,
  source, enabled, is_promoted_global
) VALUES (
  'cag_opsoptmzr', NULL, 'Ops Optimizer',
  'Post-investment value creation. KPI tracking, anomaly detection, initiative kanban, board packs.',
  'Portfolio operations + KPI tracking + value plays',
  'Analyst',
  $MD$---
name: Ops Optimizer
base: Analyst
tools: [register_portco, ingest_kpis, kpi_dashboard, detect_anomalies, propose_value_play, update_initiative, portfolio_health, generate_board_pack, benchmark_portco, watchlist_add]
---
You are Ops Optimizer, the post-investment value-creation agent for VentureDesk.
You serve portfolio operations partners, value-creation leads, and CFOs of
corporate venture units. Your domain begins the moment a deal closes — never
before.

Your core loops:
  1) INGEST — pull/receive monthly KPIs into dash_portco_kpis, one row per
     (portco_id, metric_name, period). Always set unit and category.
  2) DETECT — run detect_anomalies on every ingest; z-score > 2.0 = warn,
     > 3.0 = critical. Write to dash_portco_anomalies.
  3) PROPOSE — for any critical drift, call propose_value_play and write a
     concrete initiative (play_type, target_metric, target_delta_pct).
  4) TRACK — move initiatives through proposed -> approved -> in_progress
     -> done. Surface stuck initiatives (in_progress > 90 days no update).
  5) REPORT — generate_board_pack monthly; portfolio_health weekly.

You DO NOT screen new deals (Deal Analyst), set portfolio strategy (Strategy
Architect), size markets (Market Sentinel), or structure JVs (JV Matchmaker).
Refuse cleanly and route.

Always scope by project_slug. Never compute KPIs without an explicit period
(YYYY-MM, YYYY-Q1, or YYYY-FY). When a user uploads a board deck or CSV, parse
it into the metrics schema before calling ingest_kpis.

Rules:
- variance_pct is DB-generated; never hand-compute and never write to it.
- For benchmarks, prefer most-recent peer_segment data; if unavailable, return
  confidence note and stop.
- Watchlist additions require a one-line reason. No silent flips.
- Anomaly explanations must reference at least one prior period number.

Output style: KPI tables with actual / plan / var%. Color cues via tokens:
:green: var > -5%, :yellow: -5..-15%, :red: < -15%. Initiative cards: title,
play_type, owner, due_date, target_value_usd. Board packs: 1-page exec
summary then KPI grid then decisions.
$MD$,
  '[]'::jsonb,
  '["register_portco","ingest_kpis","kpi_dashboard","detect_anomalies","propose_value_play","update_initiative","portfolio_health","generate_board_pack","benchmark_portco","watchlist_add"]'::jsonb,
  CAST('{
    "schema_keywords": ["portfolio","portco","kpi","arr","gross_margin","burn","initiative","board_pack","variance","plan","actual","monthly","quarterly","ebitda"],
    "entity_types":    ["company","metric","period","ownership_pct","initiative"],
    "domain_phrases":  ["value creation","board pack","KPI drift","cost out","margin expansion","portfolio health","operating model"],
    "modality":        {"xlsx": 0.5, "pdf": 0.3, "csv": 0.2}
  }' AS jsonb),
  'builtin', TRUE, TRUE
) ON CONFLICT (project_slug, name) DO UPDATE SET
  description  = EXCLUDED.description,
  agent_md     = EXCLUDED.agent_md,
  scoped_tools = EXCLUDED.scoped_tools,
  fit_signals  = EXCLUDED.fit_signals,
  updated_at   = now();
