-- Migration 102: Ingest staging tables
-- Owned by: dash/ingest module
-- Tables: public.dash_ingest_batches, public.dash_ingest_files
-- NOTE: dash_ingest_contracts is owned by another agent — NOT created here.
-- All DDL is idempotent (CREATE TABLE IF NOT EXISTS, ADD COLUMN IF NOT EXISTS,
-- CREATE INDEX IF NOT EXISTS).

-- -------------------------------------------------------------------------
-- 1. dash_ingest_batches
--    One row per ingest batch.  Manifest JSONB stores the full frozen shape.
-- -------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.dash_ingest_batches (
    batch_id    TEXT        PRIMARY KEY,
    project_slug TEXT       NOT NULL,
    status      TEXT        NOT NULL DEFAULT 'staged',
    file_count  INT                  DEFAULT 0,
    manifest    JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Index for per-project lookups (newest-first queries)
CREATE INDEX IF NOT EXISTS idx_dash_ingest_batches_project_created
    ON public.dash_ingest_batches (project_slug, created_at DESC);

-- -------------------------------------------------------------------------
-- 2. dash_ingest_files
--    One row per file within a batch.  Mirrors the manifest files[] entry
--    but in relational form for efficient querying.
-- -------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.dash_ingest_files (
    id           BIGSERIAL   PRIMARY KEY,
    batch_id     TEXT        NOT NULL,
    project_slug TEXT,
    filename     TEXT,
    content_hash TEXT,
    dataset      TEXT,
    verdict      TEXT,
    target_table TEXT,
    load_key     JSONB,
    score        INT,
    status       TEXT,
    reason       TEXT,
    rows         INT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Index: look up all files belonging to a batch
CREATE INDEX IF NOT EXISTS idx_dash_ingest_files_batch_id
    ON public.dash_ingest_files (batch_id);

-- Index: deduplication check — project + content hash
CREATE INDEX IF NOT EXISTS idx_dash_ingest_files_project_hash
    ON public.dash_ingest_files (project_slug, content_hash);
