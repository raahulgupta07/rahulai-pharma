-- 122_telemetry.sql — Telemetry tables for admin observability dashboard.
-- Idempotent. Seeds aligned to caveman mockup names.

CREATE SCHEMA IF NOT EXISTS dash;

-- ----------------------------------------------------------------------------
-- Alerts firing in the system. severity = INFO|WARN|CRIT
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dash.tel_alerts (
    id            BIGSERIAL PRIMARY KEY,
    severity      TEXT NOT NULL CHECK (severity IN ('INFO','WARN','CRIT')),
    rule_name     TEXT NOT NULL,
    triggered_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    silenced      BOOLEAN NOT NULL DEFAULT FALSE,
    owner         TEXT,
    detail        TEXT
);
CREATE INDEX IF NOT EXISTS idx_tel_alerts_triggered_at
    ON dash.tel_alerts (triggered_at DESC);
CREATE INDEX IF NOT EXISTS idx_tel_alerts_silenced
    ON dash.tel_alerts (silenced);

-- ----------------------------------------------------------------------------
-- Daily cost rollup. PK is day so re-runs upsert cleanly.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dash.tel_cost_daily (
    day             DATE PRIMARY KEY,
    cost_usd        NUMERIC(12,4) NOT NULL DEFAULT 0,
    tokens_in       BIGINT NOT NULL DEFAULT 0,
    tokens_out      BIGINT NOT NULL DEFAULT 0,
    cache_hits_pct  NUMERIC(5,2) NOT NULL DEFAULT 0
);

-- ----------------------------------------------------------------------------
-- Per-tool 24h latency + error stats.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dash.tel_tool_stats (
    id          BIGSERIAL PRIMARY KEY,
    tool_name   TEXT NOT NULL UNIQUE,
    calls_24h   INTEGER NOT NULL DEFAULT 0,
    err_pct     NUMERIC(5,2) NOT NULL DEFAULT 0,
    p50_ms      INTEGER NOT NULL DEFAULT 0,
    p95_ms      INTEGER NOT NULL DEFAULT 0,
    p99_ms      INTEGER NOT NULL DEFAULT 0,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ----------------------------------------------------------------------------
-- Per-connector health snapshot.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dash.tel_connector_health (
    id            BIGSERIAL PRIMARY KEY,
    conn_name     TEXT NOT NULL UNIQUE,
    conn_type     TEXT,
    last_test_at  TIMESTAMPTZ,
    p95_ms        INTEGER NOT NULL DEFAULT 0,
    err_pct       NUMERIC(5,2) NOT NULL DEFAULT 0
);

-- ----------------------------------------------------------------------------
-- Seed: 8 tools, 5 connectors, 7 cost days, 3 alerts. Idempotent via ON CONFLICT.
-- ----------------------------------------------------------------------------
INSERT INTO dash.tel_tool_stats (tool_name, calls_24h, err_pct, p50_ms, p95_ms, p99_ms)
VALUES
    ('query_connector',  1240, 0.8,  120,  480,  920),
    ('websearch',         860, 2.4,  340, 1200, 2400),
    ('sql_exec',         3120, 1.2,   45,  220,  610),
    ('pdf_fill_fields',   145, 0.0,  210,  650, 1100),
    ('vector_search',    2050, 0.4,   38,  180,  430),
    ('run_sql_query',    4200, 0.6,   52,  240,  680),
    ('semantic_search',  1730, 0.9,   72,  310,  720),
    ('mta_summary',       310, 1.6,  180,  720, 1450)
ON CONFLICT (tool_name) DO NOTHING;

INSERT INTO dash.tel_connector_health (conn_name, conn_type, last_test_at, p95_ms, err_pct)
VALUES
    ('warehouse_pg',  'postgres',  now() - interval '5 minutes',  180, 0.2),
    ('analytics_bq',  'bigquery',  now() - interval '12 minutes', 540, 0.8),
    ('crm_mssql',     'mssql',     now() - interval '3 hours',    420, 2.1),
    ('lake_fabric',   'fabric',    now() - interval '1 hour',     680, 1.4),
    ('reports_pbi',   'powerbi',   now() - interval '30 minutes', 920, 0.5)
ON CONFLICT (conn_name) DO NOTHING;

INSERT INTO dash.tel_cost_daily (day, cost_usd, tokens_in, tokens_out, cache_hits_pct)
VALUES
    (CURRENT_DATE - 6,  18.42,  920000,  310000, 41.2),
    (CURRENT_DATE - 5,  22.18, 1080000,  385000, 38.7),
    (CURRENT_DATE - 4,  19.95,  995000,  342000, 44.1),
    (CURRENT_DATE - 3,  26.71, 1240000,  442000, 39.8),
    (CURRENT_DATE - 2,  24.03, 1150000,  398000, 42.5),
    (CURRENT_DATE - 1,  28.96, 1340000,  478000, 40.3),
    (CURRENT_DATE,      12.40,  610000,  205000, 45.6)
ON CONFLICT (day) DO NOTHING;

-- Alerts: only seed when table empty so re-runs don't double-insert.
INSERT INTO dash.tel_alerts (severity, rule_name, owner, detail)
SELECT * FROM (VALUES
    ('CRIT', 'connector.crm_mssql.error_rate',    'platform', 'CRM MSSQL error rate 2.1% over 15m window'),
    ('WARN', 'tool.websearch.p95_latency',        'agents',   'websearch p95 1200ms exceeds 1000ms SLO'),
    ('INFO', 'cost.daily.cache_hit_below_target', 'finops',   'Cache hit pct 40.3% below 50% target yesterday')
) AS v(severity, rule_name, owner, detail)
WHERE NOT EXISTS (SELECT 1 FROM dash.tel_alerts);
