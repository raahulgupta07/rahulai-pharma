-- 174_usage_unified.sql — unified usage/cost spine for the Admin "Usage" dashboard.
-- Normalizes the four per-call ledgers (platform LLM, API gateway, embeddings,
-- embed widget) into one view so a single dashboard can group/filter across all
-- usage sources. Adds the missing cost/attribution columns. Idempotent + safe.

-- 1. API gateway ledger: add cost, request_type (chat|embedding), session, status.
ALTER TABLE public.dash_apigw_usage
  ADD COLUMN IF NOT EXISTS cost_usd     NUMERIC(12,6) NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS request_type TEXT          NOT NULL DEFAULT 'chat',
  ADD COLUMN IF NOT EXISTS session_id   TEXT,
  ADD COLUMN IF NOT EXISTS status       TEXT          NOT NULL DEFAULT 'ok';

CREATE INDEX IF NOT EXISTS idx_apigw_usage_type_ts
  ON public.dash_apigw_usage(request_type, ts DESC);

-- 2. Platform LLM ledger: attribute each call to the user (actor) when known.
ALTER TABLE public.dash_llm_costs
  ADD COLUMN IF NOT EXISTS actor TEXT;

CREATE INDEX IF NOT EXISTS idx_llm_costs_actor_ts
  ON public.dash_llm_costs(actor, ts DESC);

-- 3. Embed widget ledger: add token + cost estimates (char counts already kept).
ALTER TABLE public.dash_embed_calls
  ADD COLUMN IF NOT EXISTS tokens_in  INTEGER       NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS tokens_out INTEGER       NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS cost_usd   NUMERIC(12,6) NOT NULL DEFAULT 0;

-- 4. The unified view: one normalized row shape across every usage source.
--    Columns: src, ts, actor, store_id, model, tokens_in, tokens_out, cost_usd, status.
-- DROP first: CREATE OR REPLACE VIEW cannot drop/reorder columns, so a
-- redefinition that changes the column set errors ("cannot drop columns from
-- view") on any DB where an older v_usage_unified already exists.
DROP VIEW IF EXISTS public.v_usage_unified;
CREATE VIEW public.v_usage_unified AS
  -- Platform chat + all internal (non-training) LLM calls
  SELECT 'platform'::text AS src, ts,
         COALESCE(actor, 'system')          AS actor,
         NULL::text                         AS store_id,
         model,
         COALESCE(tokens_in, 0)             AS tokens_in,
         COALESCE(tokens_out, 0)            AS tokens_out,
         COALESCE(cost_usd, 0)              AS cost_usd,
         CASE WHEN ok THEN 'ok' ELSE 'error' END AS status
    FROM public.dash_llm_costs
   WHERE COALESCE(task, '') NOT LIKE 'train%'
  UNION ALL
  -- Training (batch ingest / dream / self-learning LLM cost)
  SELECT 'training', ts, 'system', NULL, model,
         COALESCE(tokens_in, 0), COALESCE(tokens_out, 0), COALESCE(cost_usd, 0),
         CASE WHEN ok THEN 'ok' ELSE 'error' END
    FROM public.dash_llm_costs
   WHERE COALESCE(task, '') LIKE 'train%'
  UNION ALL
  -- API gateway: chat completions AND metered embeddings (request_type splits)
  SELECT CASE WHEN request_type = 'embedding' THEN 'embedding' ELSE 'api_key' END,
         ts, COALESCE(service_account, 'unknown'), store_id, model,
         COALESCE(prompt_tokens, 0), COALESCE(completion_tokens, 0),
         COALESCE(cost_usd, 0),
         COALESCE(status, 'ok')
    FROM public.dash_apigw_usage
  UNION ALL
  -- Embed widget chat (external visitors); tokens/cost are char-derived estimates
  SELECT 'embed', ts, COALESCE(external_user, 'anon'), NULL, NULL,
         COALESCE(tokens_in, 0), COALESCE(tokens_out, 0), COALESCE(cost_usd, 0),
         CASE WHEN success THEN 'ok' ELSE 'error' END
    FROM public.dash_embed_calls;

COMMENT ON VIEW public.v_usage_unified IS
  'Normalized cross-source usage/cost spine (platform|training|api_key|embedding|embed) for the Admin Usage dashboard.';
