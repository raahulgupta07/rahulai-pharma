-- Migration 081: Investment Pack — memos, runs, yfinance/exa caches
--
-- Schema-qualified `dash.*`. Idempotent (CREATE TABLE IF NOT EXISTS +
-- ALTER TABLE ADD COLUMN IF NOT EXISTS). Backs the Investment vertical:
--   - investment_memos: durable BUY/HOLD/PASS/SELL verdicts per symbol
--   - investment_runs : workflow run history + SSE event log JSONB
--   - yf_cache / exa_cache : TTL-bounded fetch caches (yfinance + exa.ai)

CREATE SCHEMA IF NOT EXISTS dash;

-- ── investment_memos ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS dash.dash_investment_memos (
    id                  BIGSERIAL PRIMARY KEY,
    project_slug        TEXT NOT NULL,
    symbol              TEXT NOT NULL,
    verdict             TEXT NOT NULL CHECK (verdict IN ('BUY','HOLD','PASS','SELL')),
    conviction          INT  NOT NULL CHECK (conviction BETWEEN 1 AND 5),
    body_md             TEXT,
    analysts_consulted  TEXT[],
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by_agent    TEXT,
    metadata            JSONB NOT NULL DEFAULT '{}'::jsonb
);

ALTER TABLE dash.dash_investment_memos
    ADD COLUMN IF NOT EXISTS analysts_consulted TEXT[];
ALTER TABLE dash.dash_investment_memos
    ADD COLUMN IF NOT EXISTS created_by_agent TEXT;
ALTER TABLE dash.dash_investment_memos
    ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE INDEX IF NOT EXISTS idx_inv_memos_proj_created
    ON dash.dash_investment_memos (project_slug, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_inv_memos_proj_sym_created
    ON dash.dash_investment_memos (project_slug, symbol, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_inv_memos_verdict
    ON dash.dash_investment_memos (verdict);

-- ── yf_cache (yfinance fundamentals/quotes) ──────────────────────────
CREATE TABLE IF NOT EXISTS dash.dash_yf_cache (
    id          BIGSERIAL PRIMARY KEY,
    cache_key   TEXT UNIQUE,
    payload     JSONB,
    cached_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_yf_cache_cached_at
    ON dash.dash_yf_cache (cached_at);

-- ── exa_cache (exa.ai news / web search) ─────────────────────────────
CREATE TABLE IF NOT EXISTS dash.dash_exa_cache (
    id          BIGSERIAL PRIMARY KEY,
    cache_key   TEXT UNIQUE,
    payload     JSONB,
    cached_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_exa_cache_cached_at
    ON dash.dash_exa_cache (cached_at);

-- ── investment_runs (workflow run log + SSE events) ──────────────────
CREATE TABLE IF NOT EXISTS dash.dash_investment_runs (
    id            BIGSERIAL PRIMARY KEY,
    project_slug  TEXT,
    symbol        TEXT,
    team_pattern  TEXT CHECK (team_pattern IN ('coordinate','route','broadcast','task','pipeline')),
    status        TEXT CHECK (status IN ('queued','running','done','failed')),
    started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at   TIMESTAMPTZ,
    memo_id       BIGINT,
    events        JSONB NOT NULL DEFAULT '[]'::jsonb,
    error         TEXT
);

ALTER TABLE dash.dash_investment_runs
    ADD COLUMN IF NOT EXISTS memo_id BIGINT;
ALTER TABLE dash.dash_investment_runs
    ADD COLUMN IF NOT EXISTS events JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE dash.dash_investment_runs
    ADD COLUMN IF NOT EXISTS error TEXT;

CREATE INDEX IF NOT EXISTS idx_inv_runs_proj_started
    ON dash.dash_investment_runs (project_slug, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_inv_runs_status
    ON dash.dash_investment_runs (status);
