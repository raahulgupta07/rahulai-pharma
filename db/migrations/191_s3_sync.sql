-- 191_s3_sync.sql — S3 auto-sync sources (poll a bucket → replace tables → retrain)
-- Idempotent. Credentials stored Fernet-encrypted (dash.connectors.crypto), never plaintext.

CREATE TABLE IF NOT EXISTS public.dash_s3_sources (
    id               SERIAL PRIMARY KEY,
    project_slug     TEXT NOT NULL DEFAULT 'citypharma',
    name             TEXT NOT NULL,
    bucket           TEXT NOT NULL,
    prefix           TEXT NOT NULL DEFAULT '',
    region           TEXT NOT NULL DEFAULT 'us-east-1',
    endpoint_url     TEXT,                       -- optional (MinIO / S3-compatible)
    creds_enc        TEXT,                       -- Fernet token of {access_key, secret_key}
    file_map         JSONB NOT NULL DEFAULT '[]'::jsonb,  -- [{pattern,table,action}]
    schedule_seconds INTEGER NOT NULL DEFAULT 300,
    retrain_after    BOOLEAN NOT NULL DEFAULT TRUE,
    enabled          BOOLEAN NOT NULL DEFAULT FALSE,
    last_sync_at     TIMESTAMPTZ,
    last_status      TEXT,                       -- ok | error | running | never
    last_log         TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Per-object change-detection state: an object is re-synced only when its ETag
-- (or last_modified) changes, so an unchanged file never re-triggers a retrain.
CREATE TABLE IF NOT EXISTS public.dash_s3_object_state (
    id            SERIAL PRIMARY KEY,
    source_id     INTEGER NOT NULL REFERENCES public.dash_s3_sources(id) ON DELETE CASCADE,
    object_key    TEXT NOT NULL,
    etag          TEXT,
    last_modified TIMESTAMPTZ,
    table_name    TEXT,
    rows_loaded   INTEGER,
    synced_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_id, object_key)
);

CREATE INDEX IF NOT EXISTS idx_s3_object_state_source ON public.dash_s3_object_state(source_id);
