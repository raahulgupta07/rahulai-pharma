-- Migration 102: Schema contract registry for the staged data-ingest pipeline.
--
-- Table: public.dash_ingest_contracts
--   Locks each logical dataset's expected column shape so that silent schema
--   drift (renamed / added / retyped columns across monthly file drops) is
--   caught before it corrupts a destination table.
--
-- Idempotent: safe to run against a DB that already has this migration applied.

-- -----------------------------------------------------------------------
-- Main contract table
-- -----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.dash_ingest_contracts (
    id             BIGSERIAL PRIMARY KEY,
    project_slug   TEXT        NOT NULL,
    dataset        TEXT        NOT NULL,
    version        INT         NOT NULL DEFAULT 1,
    active         BOOLEAN     NOT NULL DEFAULT TRUE,
    columns        JSONB,           -- {colname: pgtype} e.g. {"region":"text","sales":"double precision"}
    load_key       JSONB,           -- {"strategy":"single|composite|period|content_hash","columns":[...]}
    period_source  TEXT,            -- "filename" | "<colname>" | NULL
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- -----------------------------------------------------------------------
-- Unique index: one row per (project, dataset, version)
-- -----------------------------------------------------------------------
CREATE UNIQUE INDEX IF NOT EXISTS uq_ingest_contracts_pdv
    ON public.dash_ingest_contracts (project_slug, dataset, version);

-- -----------------------------------------------------------------------
-- Partial index: fast lookup of the active contract per (project, dataset)
-- -----------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_ingest_contracts_active
    ON public.dash_ingest_contracts (project_slug, dataset)
    WHERE active = TRUE;

-- -----------------------------------------------------------------------
-- Supporting index for listing all contracts per project
-- -----------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_ingest_contracts_project
    ON public.dash_ingest_contracts (project_slug);
