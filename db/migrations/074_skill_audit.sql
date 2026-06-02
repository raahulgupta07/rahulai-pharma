-- 074_skill_audit.sql
-- Skill Quality Pipeline audit log + schema-aware skill params support.
--
-- Adds:
--   * dash.dash_skill_audit_log — every promotion candidate's 10-check audit
--     result (pass/fail + per-check failures + candidate SQL), so regressions
--     and false positives can be debugged after the fact.
--
-- Idempotent: CREATE TABLE / INDEX IF NOT EXISTS.

CREATE TABLE IF NOT EXISTS dash.dash_skill_audit_log (
    id                BIGSERIAL PRIMARY KEY,
    skill_name        TEXT          NOT NULL,
    project_slug      TEXT          NOT NULL,
    candidate_sql     TEXT,
    audit_result      JSONB         NOT NULL DEFAULT '{}'::jsonb,
        -- {pass: bool, score: int, failures: [str], checks: {...}}
    passed            BOOLEAN       NOT NULL DEFAULT FALSE,
    created_at        TIMESTAMPTZ   NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_skill_audit_log_recent
    ON dash.dash_skill_audit_log(project_slug, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_skill_audit_log_passed
    ON dash.dash_skill_audit_log(project_slug, passed, created_at DESC);

-- ROLLBACK
-- DROP TABLE IF EXISTS dash.dash_skill_audit_log;
