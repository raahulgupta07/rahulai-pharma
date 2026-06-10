-- 182_usage_analytics.sql — model/embedding/feedback/token analytics
--
-- Adds the columns the Usage & Cost analytics tabs need:
--   • reasoning_tokens / cached_tokens on the two real LLM ledgers so the
--     Token-breakdown + Prompt-caching + cache-hit-rate panels are REAL
--     (OpenRouter returns these in usage.*_tokens_details).
--   • input_preview on dash_apigw_usage so the Embeddings tab can show WHAT
--     text each embedding call embedded (privacy-gated — only written when
--     EMBED_LOG_INPUT=1, mirrors the embed body-logging flag).
--
-- All additive + IF NOT EXISTS → safe to re-run. 2026-06-10.

ALTER TABLE public.dash_llm_costs
  ADD COLUMN IF NOT EXISTS reasoning_tokens INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS cached_tokens    INTEGER NOT NULL DEFAULT 0;

ALTER TABLE public.dash_apigw_usage
  ADD COLUMN IF NOT EXISTS reasoning_tokens INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS cached_tokens    INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS input_preview    TEXT;

-- feedback drill-downs filter by rating + recency
CREATE INDEX IF NOT EXISTS idx_dash_feedback_rating_ts
  ON public.dash_feedback (rating, created_at DESC);

-- embedding-call lookups are request_type + time scoped
CREATE INDEX IF NOT EXISTS idx_apigw_usage_reqtype_ts
  ON public.dash_apigw_usage (request_type, ts DESC);
