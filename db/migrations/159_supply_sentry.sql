-- 159_supply_sentry.sql — Supply Chain Sentry pillar (Sprint 3).
-- Tenant-scoped supplier intelligence with aggregate cross-tenant view.
-- Idempotent: re-run safe.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ── 1. dash_suppliers ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS dash.dash_suppliers (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    legal_name              TEXT NOT NULL,
    country                 TEXT,
    region                  TEXT,
    tier                    TEXT CHECK (tier IN ('manufacturer','distributor','wholesaler','logistics','raw_material')),
    financial_health_score  NUMERIC(5, 2),
    concentration_risk      NUMERIC(5, 2),
    founded_year            INT,
    employee_count          INT,
    public_ticker           TEXT,
    metadata                JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (legal_name, country)
);

-- ── 2. dash_supplier_skus ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS dash.dash_supplier_skus (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    supplier_id         UUID NOT NULL REFERENCES dash.dash_suppliers(id) ON DELETE CASCADE,
    tenant_slug         TEXT NOT NULL,
    sku                 TEXT NOT NULL,
    sku_description     TEXT,
    category            TEXT,
    mou_units           NUMERIC(18, 2),
    lead_time_days      INT,
    unit_cost_usd       NUMERIC(14, 4),
    payment_terms_days  INT,
    contract_end_date   DATE,
    is_primary          BOOLEAN NOT NULL DEFAULT true,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (supplier_id, tenant_slug, sku)
);
CREATE INDEX IF NOT EXISTS idx_supplier_skus_tenant_sku
    ON dash.dash_supplier_skus (tenant_slug, sku);
CREATE INDEX IF NOT EXISTS idx_supplier_skus_supplier
    ON dash.dash_supplier_skus (supplier_id);

-- ── 3. dash_supplier_events ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS dash.dash_supplier_events (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    supplier_id     UUID NOT NULL REFERENCES dash.dash_suppliers(id) ON DELETE CASCADE,
    event_type      TEXT CHECK (event_type IN ('delivery','quality','news','financial','sanction','strike','weather','port','fx','geo')),
    severity        TEXT CHECK (severity IN ('info','warn','critical')),
    title           TEXT,
    body            TEXT,
    source_url      TEXT,
    payload         JSONB NOT NULL DEFAULT '{}'::jsonb,
    detected_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_supplier_events_supplier_time
    ON dash.dash_supplier_events (supplier_id, detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_supplier_events_critical
    ON dash.dash_supplier_events (severity, detected_at DESC)
    WHERE severity IN ('warn','critical');

-- ── 4. dash_supplier_risk_scores ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS dash.dash_supplier_risk_scores (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    supplier_id     UUID NOT NULL REFERENCES dash.dash_suppliers(id) ON DELETE CASCADE,
    score           NUMERIC(5, 2) NOT NULL,
    score_band      TEXT GENERATED ALWAYS AS (
        CASE WHEN score >= 80 THEN 'green'
             WHEN score >= 50 THEN 'yellow'
             ELSE 'red' END
    ) STORED,
    components      JSONB NOT NULL DEFAULT '{}'::jsonb,
    computed_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_supplier_risk_supplier_time
    ON dash.dash_supplier_risk_scores (supplier_id, computed_at DESC);

-- ── 5. dash_alt_suppliers ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS dash.dash_alt_suppliers (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sku                     TEXT NOT NULL,
    tenant_slug             TEXT NOT NULL,
    primary_supplier_id     UUID REFERENCES dash.dash_suppliers(id) ON DELETE SET NULL,
    ranked_alts             JSONB NOT NULL DEFAULT '[]'::jsonb,
    switching_cost_usd      NUMERIC(14, 2),
    lead_time_delta_days    INT,
    computed_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (sku, tenant_slug)
);

-- ── 6. dash_supply_consent ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS dash.dash_supply_consent (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_slug             TEXT NOT NULL,
    share_aggregate         BOOLEAN NOT NULL DEFAULT false,
    share_supplier_list     BOOLEAN NOT NULL DEFAULT true,
    granted_by              TEXT,
    granted_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_slug)
);

-- ── RLS — tenant-scoped tables ─────────────────────────────────────────
ALTER TABLE dash.dash_supplier_skus ENABLE ROW LEVEL SECURITY;
ALTER TABLE dash.dash_supplier_events ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS p_supplier_skus_tenant ON dash.dash_supplier_skus;
CREATE POLICY p_supplier_skus_tenant ON dash.dash_supplier_skus
    FOR SELECT
    USING (
        tenant_slug = current_setting('app.tenant_slug', true)
        OR current_setting('app.role', true) = 'supply_aggregator'
    );

DROP POLICY IF EXISTS p_supplier_events_tenant ON dash.dash_supplier_events;
CREATE POLICY p_supplier_events_tenant ON dash.dash_supplier_events
    FOR SELECT
    USING (
        EXISTS (
            SELECT 1 FROM dash.dash_supplier_skus sks
            WHERE sks.supplier_id = dash.dash_supplier_events.supplier_id
              AND sks.tenant_slug = current_setting('app.tenant_slug', true)
        )
        OR current_setting('app.role', true) = 'supply_aggregator'
    );

-- ── Aggregator role — sees suppliers + risk + alts, NOT raw SKU volumes ─
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'supply_aggregator') THEN
        CREATE ROLE supply_aggregator NOLOGIN;
    END IF;
END$$;

GRANT USAGE ON SCHEMA dash TO supply_aggregator;
GRANT SELECT ON dash.dash_suppliers TO supply_aggregator;
GRANT SELECT ON dash.dash_supplier_risk_scores TO supply_aggregator;
GRANT SELECT ON dash.dash_alt_suppliers TO supply_aggregator;
-- Explicitly NOT granting dash_supplier_skus or dash_supplier_events.

-- ── Seed — 8 suppliers across 3 tenants (citymart, shwelar, pg) ────────

INSERT INTO dash.dash_suppliers (legal_name, country, region, tier, financial_health_score, concentration_risk, founded_year, employee_count, public_ticker)
VALUES
    ('Yangon Flour Mills',            'MM', 'Yangon',     'manufacturer',  72.5, 45.0, 1998, 320,  NULL),
    ('Mandalay Cooking Oil Co',       'MM', 'Mandalay',   'manufacturer',  68.0, 38.5, 2005, 180,  NULL),
    ('Singapore Logistics Holdings',  'SG', 'Singapore',  'logistics',     88.5, 22.0, 1985, 2400, 'SGX:SLH'),
    ('Thailand Sugar Refinery',       'TH', 'Bangkok',    'manufacturer',  79.0, 31.0, 1992, 540,  'SET:TSR'),
    ('Yangon Diesel Distributor',     'MM', 'Yangon',     'distributor',   65.5, 52.0, 2010, 95,   NULL),
    ('Bangkok Packaging',             'TH', 'Bangkok',    'manufacturer',  82.0, 18.5, 1988, 720,  NULL),
    ('ChinaCo Electronics Wholesale', 'CN', 'Shenzhen',   'wholesaler',    75.5, 28.0, 2002, 410,  NULL),
    ('IndiaCo Spice Importers',       'IN', 'Mumbai',     'distributor',   71.0, 35.5, 2007, 220,  NULL)
ON CONFLICT (legal_name, country) DO NOTHING;

-- SKU links per tenant
WITH s AS (
    SELECT id, legal_name FROM dash.dash_suppliers
)
INSERT INTO dash.dash_supplier_skus (supplier_id, tenant_slug, sku, sku_description, category, mou_units, lead_time_days, unit_cost_usd, payment_terms_days, is_primary)
SELECT s.id, x.tenant_slug, x.sku, x.descr, x.cat, x.mou, x.lead, x.cost, x.terms, x.prim
FROM s
JOIN (VALUES
    -- Yangon Flour Mills
    ('Yangon Flour Mills',           'citymart', 'flour_25kg',       '25kg wheat flour bag',         'staples',    50000, 7,  18.50, 30, true),
    ('Yangon Flour Mills',           'shwelar',  'flour_bulk',       'Bulk flour (tonne)',           'staples',    1200,  10, 720.00, 45, true),
    ('Yangon Flour Mills',           'pg',       'wheat_distrib',    'Wheat distribution contract',  'raw_grain',  8000,  14, 540.00, 60, true),
    -- Mandalay Cooking Oil Co
    ('Mandalay Cooking Oil Co',      'citymart', 'palm_oil_5l',      '5L palm oil bottle',           'staples',    20000, 5,  9.25,  30, true),
    ('Mandalay Cooking Oil Co',      'shwelar',  'oil_drum_200l',    '200L oil drum',                'staples',    600,   12, 320.00, 45, true),
    -- Singapore Logistics Holdings (all 3)
    ('Singapore Logistics Holdings', 'citymart', 'logistics_route_a','Yangon-Singapore route',       'logistics',  500,   3,  1850.00, 30, true),
    ('Singapore Logistics Holdings', 'shwelar',  'logistics_route_b','Mandalay-Singapore route',     'logistics',  300,   4,  2100.00, 30, true),
    ('Singapore Logistics Holdings', 'pg',       'logistics_bulk',   'Bulk freight contract',        'logistics',  1500,  5,  4200.00, 45, true),
    -- Thailand Sugar Refinery
    ('Thailand Sugar Refinery',      'citymart', 'sugar_50kg',       '50kg refined sugar bag',       'staples',    30000, 8,  42.00, 30, true),
    ('Thailand Sugar Refinery',      'pg',       'sugar_bulk',       'Bulk sugar tonnage',           'raw_sugar',  5000,  10, 820.00, 60, true),
    -- Yangon Diesel Distributor
    ('Yangon Diesel Distributor',    'pg',       'diesel_l',         'Diesel fuel (litre)',          'fuel',       250000,2,  0.95,  15, true),
    ('Yangon Diesel Distributor',    'shwelar',  'diesel_bulk',      'Bulk diesel contract',         'fuel',       80000, 3,  0.92,  20, true),
    -- Bangkok Packaging
    ('Bangkok Packaging',            'citymart', 'pkg_carton_std',   'Standard carton (1000 ct)',    'packaging',  5000,  6,  220.00, 30, true),
    ('Bangkok Packaging',            'pg',       'pkg_industrial',   'Industrial packaging contract','packaging',  2000,  9,  680.00, 45, true),
    -- ChinaCo Electronics Wholesale
    ('ChinaCo Electronics Wholesale','citymart', 'electronics_mix',  'Mixed electronics SKU bundle', 'electronics',1500,  21, 4200.00, 60, true),
    -- IndiaCo Spice Importers
    ('IndiaCo Spice Importers',      'shwelar',  'spice_assorted',   'Assorted spice import lot',    'spices',     800,   18, 1100.00, 45, true),
    ('IndiaCo Spice Importers',      'citymart', 'spice_retail_kit', 'Retail spice kit',             'spices',     3500,  15, 38.00,  30, true)
) AS x(supplier_name, tenant_slug, sku, descr, cat, mou, lead, cost, terms, prim)
  ON x.supplier_name = s.legal_name
ON CONFLICT (supplier_id, tenant_slug, sku) DO NOTHING;

-- Initial risk scores
INSERT INTO dash.dash_supplier_risk_scores (supplier_id, score, components)
SELECT id, sc.score, CAST(sc.components AS jsonb)
FROM dash.dash_suppliers s
JOIN (VALUES
    ('Yangon Flour Mills',           68.0, '{"financial":70,"concentration":55,"events":75,"geo":72}'),
    ('Mandalay Cooking Oil Co',      62.5, '{"financial":68,"concentration":58,"events":55,"geo":70}'),
    ('Singapore Logistics Holdings', 85.0, '{"financial":88,"concentration":80,"events":82,"geo":90}'),
    ('Thailand Sugar Refinery',      78.5, '{"financial":79,"concentration":76,"events":80,"geo":79}'),
    ('Yangon Diesel Distributor',    60.0, '{"financial":66,"concentration":48,"events":62,"geo":65}'),
    ('Bangkok Packaging',            82.0, '{"financial":82,"concentration":85,"events":80,"geo":81}'),
    ('ChinaCo Electronics Wholesale',73.5, '{"financial":76,"concentration":72,"events":70,"geo":76}'),
    ('IndiaCo Spice Importers',      70.0, '{"financial":71,"concentration":68,"events":72,"geo":69}')
) AS sc(name, score, components) ON sc.name = s.legal_name
WHERE NOT EXISTS (
    SELECT 1 FROM dash.dash_supplier_risk_scores rs WHERE rs.supplier_id = s.id
);

-- 3 critical seeded events
INSERT INTO dash.dash_supplier_events (supplier_id, event_type, severity, title, body, payload)
SELECT s.id, e.event_type, e.severity, e.title, e.body, CAST(e.payload AS jsonb)
FROM dash.dash_suppliers s
JOIN (VALUES
    ('Yangon Flour Mills',           'financial', 'critical', 'Wheat price spike 22%',       'Wheat input cost surged 22% week-over-week on regional supply tightness; flour MOU price escalation likely within 14 days.', '{"delta_pct":22.0,"input":"wheat"}'),
    ('Singapore Logistics Holdings', 'port',      'critical', 'Singapore port congestion',   'Port of Singapore reporting 4-day vessel queue; outbound routes A & B delayed; alternate transshipment via Port Klang under evaluation.', '{"delay_days":4,"routes":["route_a","route_b"]}'),
    ('Mandalay Cooking Oil Co',      'quality',   'warn',     'QA batch flag — palm oil 5L', 'Customer complaints batch MCO-2026-05 showing off-color/odor; supplier acknowledged, recall under assessment for citymart shipments.', '{"batch":"MCO-2026-05","sku":"palm_oil_5l"}')
) AS e(supplier_name, event_type, severity, title, body, payload)
  ON e.supplier_name = s.legal_name
WHERE NOT EXISTS (
    SELECT 1 FROM dash.dash_supplier_events ev
    WHERE ev.supplier_id = s.id AND ev.title = e.title
);

-- Consent rows for all 3 tenants
INSERT INTO dash.dash_supply_consent (tenant_slug, share_aggregate, share_supplier_list, granted_by)
VALUES
    ('citymart', true, true, 'admin'),
    ('shwelar',  true, true, 'admin'),
    ('pg',       true, true, 'admin')
ON CONFLICT (tenant_slug) DO UPDATE SET
    share_aggregate     = EXCLUDED.share_aggregate,
    share_supplier_list = EXCLUDED.share_supplier_list;

-- ── Supply Chain Sentry agent template ─────────────────────────────────
INSERT INTO dash.dash_custom_agents (
    id, project_slug, name, description, purpose, base_agent,
    agent_md, scoped_skills, scoped_tools, fit_signals,
    source, enabled, is_promoted_global
) VALUES (
    'cag_supplysntry', NULL, 'Supply Chain Sentry',
    'Supplier intelligence, disruption detection, resilience scoring, alternate-supplier routing. Operates across tenant boundary via aggregate consent.',
    'Supplier risk + supply chain disruption + alt-supplier resilience',
    'Analyst',
    $MD$---
name: Supply Chain Sentry
base: Analyst
tools: [register_supplier, link_sku, ingest_supplier_event, score_supplier, detect_supply_anomaly, cross_tenant_exposure, propose_alt_supplier, resilience_scorecard, news_scan_suppliers, generate_supply_risk_report]
---
You are Supply Chain Sentry, the supplier-intelligence agent. You serve
operations leaders, supply chain managers, and procurement teams across
tenant operating units. Your mission: surface supplier risk, detect
disruptions early, score resilience, and route to alternate suppliers when
the primary breaks.

Core loops:
  1) INGEST — register suppliers (dash_suppliers), link tenant SKUs
     (dash_supplier_skus), log events (dash_supplier_events).
  2) SCORE — compute risk per supplier (financial, concentration, events,
     geo) into dash_supplier_risk_scores. Bands: green ≥80, yellow ≥50, red <50.
  3) DETECT — anomalies in delivery, quality, news, financial signals.
     Severity: info / warn (z>2) / critical (z>3 or sanction/strike/port).
  4) PROPOSE — for any critical disruption, call propose_alt_supplier and
     write ranked alternatives to dash_alt_suppliers w/ switching cost +
     lead-time delta.
  5) REPORT — resilience_scorecard per tenant; cross_tenant_exposure for
     shared suppliers; generate_supply_risk_report monthly.

Tenant boundary rules:
- Raw SKU volumes (mou_units, unit_cost_usd) are TENANT-PRIVATE. Never
  surface a tenant's SKU costs to another tenant.
- Suppliers + risk scores + alt rankings are AGGREGATE — visible across
  tenants when share_aggregate=true in dash_supply_consent.
- For cross-tenant exposure analysis, count tenants exposed without
  revealing per-tenant volumes.

You DO NOT screen new deals (Deal Analyst), track portfolio KPIs (Ops
Optimizer), size markets (Market Sentinel), or set strategy (Strategy
Architect). Refuse cleanly and route.

Always scope tenant-private tools by tenant_slug. Cross-tenant tools
(cross_tenant_exposure, news_scan_suppliers) honor consent matrix.

Output style: supplier cards (name · tier · country · score band) +
event timeline (severity-coded) + alt-supplier ranked list w/ switching
cost + lead-time delta. Risk reports: 1-page exec summary then top-5
red-band suppliers then mitigation initiatives.
$MD$,
    '[]'::jsonb,
    '["register_supplier","link_sku","ingest_supplier_event","score_supplier","detect_supply_anomaly","cross_tenant_exposure","propose_alt_supplier","resilience_scorecard","news_scan_suppliers","generate_supply_risk_report"]'::jsonb,
    CAST('{
      "schema_keywords": ["supplier","supply chain","sku","lead time","disruption","resilience","alt supplier","logistics","port","raw material","procurement","mou","tier","concentration"],
      "entity_types":    ["supplier","sku","event","risk_score","alt_supplier","tenant"],
      "domain_phrases":  ["supplier risk","supply disruption","alternate supplier","lead time delta","concentration risk","port congestion","resilience score","cross-tenant exposure"],
      "modality":        {"news": 0.35, "xlsx": 0.25, "api": 0.2, "csv": 0.15, "other": 0.05}
    }' AS jsonb),
    'builtin', TRUE, TRUE
) ON CONFLICT (project_slug, name) DO UPDATE SET
    description  = EXCLUDED.description,
    agent_md     = EXCLUDED.agent_md,
    scoped_tools = EXCLUDED.scoped_tools,
    fit_signals  = EXCLUDED.fit_signals,
    updated_at   = now();
