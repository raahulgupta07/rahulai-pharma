-- 079_compliance_pack.sql
-- Compliance Pack: DP budget + tenant_namespace + PII audit
-- Idempotent. Non-destructive on existing data.

CREATE SCHEMA IF NOT EXISTS dash;

-- ----------------------------------------------------------------------------
-- 1. Differential-privacy per-(project, user, day) budget tracker
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dash.dash_dp_budget (
    project_slug TEXT NOT NULL,
    user_id      INT  NOT NULL,
    date         DATE NOT NULL,
    budget_used  NUMERIC(8,4) NOT NULL DEFAULT 0,
    budget_max   NUMERIC(8,4) NOT NULL DEFAULT 10.0,
    PRIMARY KEY (project_slug, user_id, date)
);

CREATE INDEX IF NOT EXISTS idx_dp_budget_date
    ON dash.dash_dp_budget (date DESC);

-- ----------------------------------------------------------------------------
-- 2. Per-tenant vector isolation column + partial unique dedup index
-- ----------------------------------------------------------------------------
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'dash' AND table_name = 'dash_vectors'
    ) THEN
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'dash'
              AND table_name = 'dash_vectors'
              AND column_name = 'tenant_namespace'
        ) THEN
            EXECUTE 'ALTER TABLE dash.dash_vectors
                     ADD COLUMN tenant_namespace TEXT NOT NULL DEFAULT ''default''';
        END IF;

        EXECUTE 'CREATE INDEX IF NOT EXISTS idx_dash_vectors_tenant
                 ON dash.dash_vectors (project_slug, tenant_namespace)';

        -- Partial unique index for per-tenant dedup on text_hash
        IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'dash'
              AND table_name = 'dash_vectors'
              AND column_name = 'text_hash'
        ) THEN
            EXECUTE 'CREATE UNIQUE INDEX IF NOT EXISTS uq_dash_vectors_tenant_hash
                     ON dash.dash_vectors (project_slug, tenant_namespace, text_hash)
                     WHERE text_hash IS NOT NULL';
        END IF;
    ELSE
        RAISE NOTICE 'dash.dash_vectors not found; skipping tenant_namespace migration';
    END IF;
END $$;

-- ----------------------------------------------------------------------------
-- 3. PII detection audit log (for compliance)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dash.dash_pii_audit (
    id              BIGSERIAL PRIMARY KEY,
    project_slug    TEXT,
    user_id         INT,
    text_snippet    TEXT,
    detected_types  TEXT[],
    action_taken    TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_pii_audit_project_created
    ON dash.dash_pii_audit (project_slug, created_at DESC);
