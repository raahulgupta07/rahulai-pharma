-- 194_dynamic_schema.sql — dynamic-schema support (Phase 0 of docs/plans/dynamic_schema.md)
--
-- Two append-only / registry tables that let ingest adapt to changing source
-- schemas instead of failing on a column rename/add/drop:
--   (1) dash_schema_events  — audit log of every schema change seen during ingest
--   (2) dash_column_map     — logical->physical column registry (populated in Phase 2)
--
-- Idempotent. Both live in the public schema alongside the other dash_* tables.

-- (1) Append-only audit of every schema change detected during ingest.
CREATE TABLE IF NOT EXISTS public.dash_schema_events (
    id            BIGSERIAL   PRIMARY KEY,
    project_slug  TEXT        NOT NULL,
    table_name    TEXT        NOT NULL,
    event         TEXT        NOT NULL,                      -- 'added' | 'removed' | 'renamed' | 'type_changed' | 'empty' | 'adapt' | 'rebuild'
    detail        JSONB       NOT NULL DEFAULT '{}'::jsonb,  -- structured payload for the event
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_schema_events_project_table
  ON public.dash_schema_events (project_slug, table_name, created_at DESC);

-- (2) Logical->physical column registry. Maps a stable logical name
-- (e.g. 'brand') to whatever physical column the live table actually has
-- (e.g. 'brand_name'), so tools/queries stay correct across source renames.
-- Populated in Phase 2 (seed/auto/confirmed) and gated by a review status.
CREATE TABLE IF NOT EXISTS public.dash_column_map (
    id            BIGSERIAL   PRIMARY KEY,
    project_slug  TEXT        NOT NULL,
    table_logical TEXT        NOT NULL,                  -- logical table name, e.g. 'catalog' / 'stock'
    logical_name  TEXT        NOT NULL,                  -- logical column, e.g. 'brand'
    physical_col  TEXT        NOT NULL,                  -- physical column, e.g. 'brand_name'
    confidence    NUMERIC     NOT NULL DEFAULT 1.0,
    source        TEXT        NOT NULL DEFAULT 'seed',   -- 'seed' | 'auto' | 'confirmed'
    status        TEXT        NOT NULL DEFAULT 'active', -- 'active' | 'pending' | 'rejected'
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (project_slug, table_logical, logical_name)
);

CREATE INDEX IF NOT EXISTS idx_column_map_project_table
  ON public.dash_column_map (project_slug, table_logical);
