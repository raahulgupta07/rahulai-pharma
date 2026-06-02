-- Verified-reward scores: a HARD truth signal per chat (did the answer match a
-- pinned/verified number?), complementing the fuzzy LLM judge in dash_quality_scores.
-- One row per chat answer that we could check against a proven Q&A / pinned metric.
CREATE TABLE IF NOT EXISTS public.dash_verified_scores (
    id            SERIAL PRIMARY KEY,
    project_slug  TEXT NOT NULL,
    session_id    TEXT,
    question      TEXT,
    verified      TEXT NOT NULL DEFAULT 'unknown',  -- pass | fail | unknown
    expected      DOUBLE PRECISION,                 -- truth number (from proven SQL / pin)
    got           DOUBLE PRECISION,                 -- number found in the answer
    source_q      TEXT,                             -- the proven question used as oracle
    created_at    TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_verified_scores_slug ON public.dash_verified_scores (project_slug, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_verified_scores_session ON public.dash_verified_scores (session_id, created_at DESC);
