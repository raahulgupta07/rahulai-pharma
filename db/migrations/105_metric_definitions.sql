-- Migration 105: Per-project user-configurable metric definitions
-- Idempotent: IF NOT EXISTS, ON CONFLICT DO NOTHING
-- Runner inserts filename into public.dash_migrations after applying.

-- ── Metric definitions (one row per business metric per project) ──────────────
CREATE TABLE IF NOT EXISTS public.dash_metric_definitions (
    id              bigserial PRIMARY KEY,
    project_slug    text        NOT NULL,
    name            text        NOT NULL,
    synonyms        jsonb       NOT NULL DEFAULT '[]',
    description     text,
    kind            text        NOT NULL DEFAULT 'count',
    -- count | rate | ratio | contribution | sum | avg
    source_glob     text,
    source_tables   jsonb       NOT NULL DEFAULT '[]',
    measure_col     text,
    filters         jsonb       NOT NULL DEFAULT '[]',
    -- [{col, op, value, trim}]
    denom_filters   jsonb       NOT NULL DEFAULT '[]',
    group_dims      jsonb       NOT NULL DEFAULT '[]',
    default_group   jsonb       NOT NULL DEFAULT '[]',
    trim_values     boolean     NOT NULL DEFAULT true,
    verified_answer jsonb,
    status          text        NOT NULL DEFAULT 'draft',
    -- draft | verified | deprecated
    version         int         NOT NULL DEFAULT 1,
    created_by      text,
    updated_by      text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (project_slug, name)
);

-- ── Version history (immutable append-only) ──────────────────────────────────
CREATE TABLE IF NOT EXISTS public.dash_metric_versions (
    id            bigserial PRIMARY KEY,
    metric_id     bigint      NOT NULL,
    project_slug  text        NOT NULL,
    name          text        NOT NULL,
    snapshot      jsonb,
    change_type   text,
    changed_by    text,
    change_reason text,
    created_at    timestamptz NOT NULL DEFAULT now()
);

-- ── Indexes ──────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_dash_metric_defs_slug_status
    ON public.dash_metric_definitions (project_slug, status);

CREATE INDEX IF NOT EXISTS idx_dash_metric_defs_slug_name
    ON public.dash_metric_definitions (project_slug, name);

CREATE INDEX IF NOT EXISTS idx_dash_metric_versions_metric_id
    ON public.dash_metric_versions (metric_id);

CREATE INDEX IF NOT EXISTS idx_dash_metric_versions_slug
    ON public.dash_metric_versions (project_slug);
