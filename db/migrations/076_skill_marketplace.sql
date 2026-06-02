-- Migration 076: Cross-Project Skill Marketplace
--
-- Promotes vetted project-scoped skills (dash.dash_skill_library) into a
-- global marketplace pool, scoped by template_name (vertical). New projects
-- bootstrap from the pool matching their template — solves cold-start.
--
-- Depends on: 067_dream_wave2.sql (dash.dash_skill_library).
-- Idempotent (IF NOT EXISTS throughout).

CREATE TABLE IF NOT EXISTS dash.dash_skill_marketplace (
    id                          BIGSERIAL PRIMARY KEY,
    name                        TEXT          NOT NULL,
    description                 TEXT          NOT NULL,
    sql_template                TEXT          NOT NULL,
        -- with ${schema}.table placeholders (parameterized at install time)
    params_schema               JSONB         NOT NULL DEFAULT '{}'::jsonb,
    template_name               TEXT          NOT NULL,
        -- vertical tag: 'pharmacy' | 'retail' | 'saas' | 'generic' | ...
    source_project_slug         TEXT,
    nominator_user_id           INT,
    avg_judge_score             NUMERIC(3,2),
    source_success_count        INT           NOT NULL DEFAULT 0,
    install_count               INT           NOT NULL DEFAULT 0,
    total_installs_succeeded    INT           NOT NULL DEFAULT 0,
    total_installs_failed       INT           NOT NULL DEFAULT 0,
    description_embedding       TEXT,
        -- pgvector serialized (1536), nullable until embed daemon runs
    status                      TEXT          NOT NULL DEFAULT 'active',
        -- active | deprecated | flagged
    tags                        TEXT[]        NOT NULL DEFAULT ARRAY[]::TEXT[],
    created_at                  TIMESTAMPTZ   NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_skill_marketplace_name_template
    ON dash.dash_skill_marketplace(name, template_name);

CREATE INDEX IF NOT EXISTS idx_skill_marketplace_template_installs
    ON dash.dash_skill_marketplace(template_name, install_count DESC);

CREATE INDEX IF NOT EXISTS idx_skill_marketplace_status
    ON dash.dash_skill_marketplace(status);

CREATE INDEX IF NOT EXISTS idx_skill_marketplace_tags
    ON dash.dash_skill_marketplace USING GIN(tags);

-- ROLLBACK
-- DROP TABLE IF EXISTS dash.dash_skill_marketplace CASCADE;
