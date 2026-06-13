-- 192_keyword_topics.sql — LLM topic-cluster rollup (privacy-safe analytics).
--
-- Part of the manager privacy policy: dashboards show keyword/topic analysis
-- ONLY, never raw questions or answers. The /keywords endpoint does live SQL
-- term-frequency; this table holds the OPTIONAL deeper LLM clustering produced by
-- the keyword_topics daemon (default OFF, KEYWORD_TOPICS_ENABLED=1).
--
-- Stores AGGREGATES ONLY — a topic label + count + a few representative keywords.
-- Never any raw question/answer text. Idempotent.

CREATE TABLE IF NOT EXISTS public.dash_keyword_topics (
  id           BIGSERIAL PRIMARY KEY,
  window_start TIMESTAMPTZ NOT NULL,
  window_end   TIMESTAMPTZ NOT NULL,
  topic        TEXT        NOT NULL,           -- LLM-assigned topic label
  count        INTEGER     NOT NULL DEFAULT 0, -- # questions in this cluster
  pct          NUMERIC     NOT NULL DEFAULT 0, -- share of the window
  keywords     JSONB       NOT NULL DEFAULT '[]'::jsonb, -- representative terms (no full text)
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_keyword_topics_window
  ON public.dash_keyword_topics (window_end DESC);
