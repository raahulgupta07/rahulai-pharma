-- Dash-OS Phase 2D — Web research compression cache + stats

CREATE TABLE IF NOT EXISTS dash.dash_compression_cache (
  cache_key TEXT PRIMARY KEY,           -- sha256(url + query_intent[:200])
  url TEXT NOT NULL,
  original_chars INTEGER NOT NULL,
  compressed_chars INTEGER NOT NULL,
  query_intent TEXT,
  compressed_text TEXT NOT NULL,
  model_used TEXT,
  cost_usd NUMERIC DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  hit_count INTEGER NOT NULL DEFAULT 0,
  last_hit_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS dash.dash_compression_stats (
  id BIGSERIAL PRIMARY KEY,
  project_slug TEXT,
  user_id INTEGER,
  run_id TEXT,
  query TEXT,
  raw_chars BIGINT,
  compressed_chars BIGINT,
  results_in INTEGER,
  results_out INTEGER,
  dedup_skipped INTEGER,
  cost_usd NUMERIC,
  latency_ms INTEGER,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_compcache_url ON dash.dash_compression_cache(url);
CREATE INDEX IF NOT EXISTS idx_compcache_age ON dash.dash_compression_cache(created_at);
CREATE INDEX IF NOT EXISTS idx_compstats_recent ON dash.dash_compression_stats(created_at DESC);
