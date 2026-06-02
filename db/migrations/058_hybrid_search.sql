-- 058_hybrid_search.sql
-- Hybrid retrieval (BM25 + vector + RRF + multi-query expansion) for Dash.
--
-- Target chunk table: dash.dash_vectors (created in 028_vectors.sql).
-- Column with chunk text is `text` (not `content`). `tsv` and the GIN index
-- already exist in 028 — every statement here is IF NOT EXISTS so the
-- migration is safe on fresh + existing DBs alike.

-- Ensure tsvector column exists on the chunk table.
ALTER TABLE dash.dash_vectors
  ADD COLUMN IF NOT EXISTS tsv tsvector
  GENERATED ALWAYS AS (to_tsvector('english', coalesce(text, ''))) STORED;

-- GIN index on the tsvector for BM25 / ts_rank_cd lookups.
CREATE INDEX IF NOT EXISTS dash_vec_fts ON dash.dash_vectors USING gin (tsv);

-- Search log: one row per /api/retrieval/search call.
CREATE TABLE IF NOT EXISTS dash.dash_search_log (
  id           BIGSERIAL PRIMARY KEY,
  project_slug TEXT,
  query        TEXT,
  mode         TEXT,
  n_results    INT,
  latency_ms   INT,
  ts           TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS dash_search_log_proj_ts
  ON dash.dash_search_log (project_slug, ts DESC);
