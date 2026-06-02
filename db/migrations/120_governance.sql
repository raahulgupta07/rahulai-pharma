-- 120_governance.sql — Governance backend tables + seed demo rows
-- Idempotent: re-applies cleanly on populated DB.

-- 1) Policies
CREATE TABLE IF NOT EXISTS dash.gov_policies (
    id           SERIAL PRIMARY KEY,
    name         TEXT NOT NULL UNIQUE,
    type         TEXT NOT NULL CHECK (type IN ('redact','guardrail','gate','block')),
    scope        TEXT NOT NULL DEFAULT 'global',
    status       TEXT NOT NULL DEFAULT 'DRAFT' CHECK (status IN ('ACTIVE','DRAFT','DISABLED')),
    yaml_body    TEXT NOT NULL DEFAULT '',
    hits_24h     INTEGER NOT NULL DEFAULT 0,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_gov_policies_status ON dash.gov_policies(status);

-- 2) Approvals
CREATE TABLE IF NOT EXISTS dash.gov_approvals (
    id              SERIAL PRIMARY KEY,
    req_id          TEXT NOT NULL UNIQUE,
    user_id         TEXT,
    resource        TEXT,
    cost_estimate   NUMERIC(12,2) DEFAULT 0,
    requested_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    status          TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','APPROVED','DENIED')),
    decided_at      TIMESTAMPTZ,
    decided_by      TEXT
);
CREATE INDEX IF NOT EXISTS idx_gov_approvals_status ON dash.gov_approvals(status);

-- 3) Data Zones
CREATE TABLE IF NOT EXISTS dash.gov_data_zones (
    id              SERIAL PRIMARY KEY,
    zone_name       TEXT NOT NULL UNIQUE,
    region          TEXT,
    datasets_count  INTEGER NOT NULL DEFAULT 0,
    classification  TEXT,
    egress          TEXT NOT NULL DEFAULT 'blocked' CHECK (egress IN ('blocked','vpn-only','open'))
);

-- 4) PII Rules
CREATE TABLE IF NOT EXISTS dash.gov_pii_rules (
    id              SERIAL PRIMARY KEY,
    pattern_name    TEXT NOT NULL UNIQUE,
    regex           TEXT NOT NULL,
    action          TEXT NOT NULL DEFAULT 'mask' CHECK (action IN ('mask','tokenize','allow-log','block')),
    matches_24h     INTEGER NOT NULL DEFAULT 0,
    owner           TEXT
);

-- 5) Retention
CREATE TABLE IF NOT EXISTS dash.gov_retention (
    id                SERIAL PRIMARY KEY,
    object_name       TEXT NOT NULL UNIQUE,
    ttl_days          INTEGER NOT NULL DEFAULT 90,
    soft_delete_days  INTEGER NOT NULL DEFAULT 30,
    hard_delete_days  INTEGER NOT NULL DEFAULT 365,
    next_purge_at     TIMESTAMPTZ,
    est_rows          BIGINT DEFAULT 0
);

-- 6) Audit Hooks
CREATE TABLE IF NOT EXISTS dash.gov_audit_hooks (
    id              SERIAL PRIMARY KEY,
    hook_name       TEXT NOT NULL UNIQUE,
    sink_url        TEXT NOT NULL,
    events_per_min  INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','failing','disabled'))
);

-- 7) Compliance Map
CREATE TABLE IF NOT EXISTS dash.gov_compliance_map (
    id              SERIAL PRIMARY KEY,
    control_id      TEXT NOT NULL,
    framework       TEXT NOT NULL,
    mapped_to       TEXT,
    coverage_pct    INTEGER NOT NULL DEFAULT 0 CHECK (coverage_pct >= 0 AND coverage_pct <= 100),
    last_review_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_gov_compliance_framework ON dash.gov_compliance_map(framework);

-- ============================================================================
-- SEED DATA (idempotent)
-- ============================================================================

INSERT INTO dash.gov_policies (name, type, scope, status, yaml_body, hits_24h) VALUES
  ('pii-mask-v2',       'redact',    'global',  'ACTIVE',   'pattern: email|ssn|phone\naction: mask', 1247),
  ('sql-readonly',      'guardrail', 'analyst', 'ACTIVE',   'allow: [SELECT, WITH, EXPLAIN]\ndeny: [DROP, ALTER, TRUNCATE]', 893),
  ('approval-spend',    'gate',      'global',  'ACTIVE',   'threshold_usd: 10\nrequire: super_admin', 42),
  ('no-export-prod',    'block',     'prod',    'ACTIVE',   'deny_egress: [s3, gdrive, email]', 18),
  ('chat-pii-redact',   'redact',    'chat',    'DRAFT',    'redact_on: [user_input, agent_output]', 0)
ON CONFLICT (name) DO NOTHING;

INSERT INTO dash.gov_approvals (req_id, user_id, resource, cost_estimate, status) VALUES
  ('req-2026-0001', 'alice@city.ai',  'deep-deck-gen',     12.50, 'PENDING'),
  ('req-2026-0002', 'bob@city.ai',    'bulk-embed-50k',    34.00, 'PENDING'),
  ('req-2026-0003', 'carol@city.ai',  'kg-rebuild-full',   8.75,  'APPROVED'),
  ('req-2026-0004', 'dan@city.ai',    'vision-pdf-batch',  21.30, 'DENIED'),
  ('req-2026-0005', 'eve@city.ai',    'forecast-90d',      4.20,  'PENDING')
ON CONFLICT (req_id) DO NOTHING;

UPDATE dash.gov_approvals SET decided_at = now() - INTERVAL '2 hours', decided_by = 'admin'
  WHERE req_id IN ('req-2026-0003','req-2026-0004') AND decided_at IS NULL;

INSERT INTO dash.gov_data_zones (zone_name, region, datasets_count, classification, egress) VALUES
  ('prod-us-east',  'us-east-1', 42, 'restricted',   'blocked'),
  ('prod-eu-west',  'eu-west-1', 28, 'restricted',   'vpn-only'),
  ('staging',       'us-west-2', 15, 'internal',     'vpn-only'),
  ('sandbox',       'us-east-1', 7,  'public',       'open')
ON CONFLICT (zone_name) DO NOTHING;

INSERT INTO dash.gov_pii_rules (pattern_name, regex, action, matches_24h, owner) VALUES
  ('email',        '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}', 'mask',       342, 'security'),
  ('ssn-us',       '\d{3}-\d{2}-\d{4}',                                'block',      12,  'security'),
  ('phone-e164',   '\+?[1-9]\d{1,14}',                                 'tokenize',   189, 'data-eng'),
  ('credit-card',  '\d{4}[ -]?\d{4}[ -]?\d{4}[ -]?\d{4}',              'block',      3,   'security'),
  ('ip-address',   '\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}',               'allow-log',  567, 'infra')
ON CONFLICT (pattern_name) DO NOTHING;

INSERT INTO dash.gov_retention (object_name, ttl_days, soft_delete_days, hard_delete_days, est_rows) VALUES
  ('chat_logs',          90,  30, 365, 1247000),
  ('agent_traces',       30,  7,  90,  4820000),
  ('upload_files_raw',   180, 60, 730, 18400),
  ('audit_log',          365, 90, 2555, 892000),
  ('embedding_vectors',  730, 180, 1825, 234000)
ON CONFLICT (object_name) DO NOTHING;

UPDATE dash.gov_retention SET next_purge_at = now() + INTERVAL '1 day' WHERE next_purge_at IS NULL;

INSERT INTO dash.gov_audit_hooks (hook_name, sink_url, events_per_min, status) VALUES
  ('splunk-prod',     'https://splunk.city.ai/hec/raw',     412, 'active'),
  ('datadog-audit',   'https://api.datadoghq.com/v1/logs',  287, 'active'),
  ('s3-archive',      's3://city-audit-archive/dash/',      105, 'active'),
  ('slack-security',  'https://hooks.slack.com/services/T0/B0/XXX', 3, 'failing')
ON CONFLICT (hook_name) DO NOTHING;

INSERT INTO dash.gov_compliance_map (control_id, framework, mapped_to, coverage_pct, last_review_at) VALUES
  ('SOC2-CC6.1',   'SOC2',     'pii-mask-v2 + sql-readonly',     94, now() - INTERVAL '14 days'),
  ('SOC2-CC7.2',   'SOC2',     'audit-hooks + retention',        88, now() - INTERVAL '30 days'),
  ('GDPR-Art32',   'GDPR',     'pii-mask-v2 + no-export-prod',   91, now() - INTERVAL '7 days'),
  ('HIPAA-164.312','HIPAA',    'pii-mask-v2 + data-zones',       76, now() - INTERVAL '45 days'),
  ('ISO27001-A8',  'ISO27001', 'data-zones + retention',         82, now() - INTERVAL '21 days')
ON CONFLICT DO NOTHING;
