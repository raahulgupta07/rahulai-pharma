-- File-hash-keyed cache of upload extraction plans
CREATE TABLE IF NOT EXISTS public.dash_upload_cache (
    file_hash TEXT PRIMARY KEY,
    file_size_bytes BIGINT,
    file_ext TEXT,
    plan JSONB NOT NULL,
    rescue_used BOOLEAN DEFAULT FALSE,
    hit_count INTEGER DEFAULT 0,
    first_seen_at TIMESTAMPTZ DEFAULT now(),
    last_used_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_upload_cache_last_used
    ON public.dash_upload_cache (last_used_at DESC);
