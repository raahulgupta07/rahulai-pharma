-- 185_answer_cache.sql — [P1] repeated-common-question answer cache.
-- Stores the FULL rendered answer (AnswerCard tags) for a question so a
-- semantic match can serve it verbatim with ZERO LLM. Question embeddings live
-- in dash.dash_vectors (namespace='qcache', source_id = this row's id) — reused
-- HNSW cosine index, no new vector store.
--
-- Freshness: each row stamps the source tables' schema_hash (cols-only, from
-- dash_table_metadata.col_hash via dash/learning/schema_guard). Serve compares
-- vs live and skips on drift — same guard as the P0 metric_shortcut gate.

CREATE TABLE IF NOT EXISTS dash.dash_answer_cache (
  id             BIGSERIAL PRIMARY KEY,
  project_slug   TEXT NOT NULL,
  question       TEXT NOT NULL,
  question_norm  TEXT NOT NULL,
  canonical_sql  TEXT,
  answer_payload JSONB NOT NULL DEFAULT '{}'::jsonb,  -- {content, sql, row_count, ...}
  source_tables  TEXT[] NOT NULL DEFAULT '{}',
  schema_hash    TEXT,
  confidence     NUMERIC NOT NULL DEFAULT 1.0,
  hit_count      INTEGER NOT NULL DEFAULT 0,
  status         TEXT NOT NULL DEFAULT 'live',         -- live | stale | demoted
  promoted_by    TEXT NOT NULL DEFAULT 'admin',        -- admin | leader
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_served_at TIMESTAMPTZ,
  UNIQUE (project_slug, question_norm)
);

CREATE INDEX IF NOT EXISTS dash_answer_cache_live
  ON dash.dash_answer_cache (project_slug, status);
