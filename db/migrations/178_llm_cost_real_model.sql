-- 178_llm_cost_real_model.sql — real LLM model id + non-zero cost for the
-- gateway (api_key) and embed paths. Idempotent + fail-soft.
--
-- WHY: v_usage_unified.cost_usd was $0 for api_key/embed because
--   (1) gateway logged the caller's OpenAI *alias* (e.g. "citypharma-analyst")
--       into dash_apigw_usage.model → _apigw_cost() found no price → 0;
--   (2) embed never captured tokens, so there was nothing to price.
-- FIX: store the real engine model (engine_model) and price off it. App code
-- (api_gateway._log_usage, embed_public) now fills engine_model + cost going
-- forward; this migration adds the columns, backfills existing rows, and
-- rewrites the view to surface engine_model as the model for cost breakdown.
--
-- NOTE: this rewrites v_usage_unified in its LIVE shape (dash_llm_costs +
-- dash_apigw_usage + dash_embed_calls), NOT migration 175's dash_traces spine
-- (175 is logically-applied but the live view is 174's — drift). Keep the live
-- shape; only the model column expression changes.

ALTER TABLE public.dash_apigw_usage  ADD COLUMN IF NOT EXISTS engine_model TEXT;
ALTER TABLE public.dash_embed_calls  ADD COLUMN IF NOT EXISTS engine_model TEXT;

-- Backfill engine_model on existing rows from request_type (best-effort: the
-- gateway chat tier runs gemini-3-flash, embeddings run the embedding model).
UPDATE public.dash_apigw_usage SET engine_model =
  CASE WHEN request_type = 'embedding'
       THEN 'google/gemini-embedding-2-preview'
       ELSE 'google/gemini-3-flash-preview' END
 WHERE engine_model IS NULL;

UPDATE public.dash_embed_calls SET engine_model = 'google/gemini-3-flash-preview'
 WHERE engine_model IS NULL;

-- One-time cost backfill for existing 0-cost gateway chat rows that DO carry
-- token counts. Prices match dash.settings._MODEL_PRICES_PER_1M (per-1M USD):
--   gemini-3-flash: in $0.30 / out $1.20 ; embedding: in $0.10 / out $0.
UPDATE public.dash_apigw_usage
   SET cost_usd = ROUND((COALESCE(prompt_tokens,0) * 0.30
                       + COALESCE(completion_tokens,0) * 1.20) / 1000000.0, 6)
 WHERE COALESCE(cost_usd,0) = 0
   AND request_type <> 'embedding'
   AND (COALESCE(prompt_tokens,0) > 0 OR COALESCE(completion_tokens,0) > 0);

UPDATE public.dash_apigw_usage
   SET cost_usd = ROUND((COALESCE(prompt_tokens,0) * 0.10) / 1000000.0, 6)
 WHERE COALESCE(cost_usd,0) = 0
   AND request_type = 'embedding'
   AND COALESCE(prompt_tokens,0) > 0;

-- Rewrite the unified spine: api_key/embedding/embed now expose engine_model
-- (real LLM) as the model column so "BY MODEL (COST)" shows the actual model.
-- DROP first — column list/order unchanged but expression differs; REPLACE is
-- fine, DROP+CREATE is explicit and safe.
DROP VIEW IF EXISTS public.v_usage_unified;
CREATE VIEW public.v_usage_unified AS
  -- platform chat (real cost ledger)
  SELECT 'platform'::text AS src, ts,
         COALESCE(actor, 'system') AS actor, NULL::text AS store_id, model,
         COALESCE(tokens_in, 0) AS tokens_in, COALESCE(tokens_out, 0) AS tokens_out,
         COALESCE(cost_usd, 0) AS cost_usd,
         CASE WHEN ok THEN 'ok' ELSE 'error' END AS status
    FROM public.dash_llm_costs
   WHERE COALESCE(task, '') NOT LIKE 'train%'
  UNION ALL
  -- training / learning runs
  SELECT 'training'::text, ts, 'system'::text, NULL::text, model,
         COALESCE(tokens_in, 0), COALESCE(tokens_out, 0), COALESCE(cost_usd, 0),
         CASE WHEN ok THEN 'ok' ELSE 'error' END
    FROM public.dash_llm_costs
   WHERE COALESCE(task, '') LIKE 'train%'
  UNION ALL
  -- gateway: chat (api_key) + embeddings — model = real engine model
  SELECT CASE WHEN request_type = 'embedding' THEN 'embedding' ELSE 'api_key' END,
         ts, COALESCE(service_account, 'unknown'), store_id,
         COALESCE(NULLIF(engine_model, ''), model),
         COALESCE(prompt_tokens, 0), COALESCE(completion_tokens, 0),
         COALESCE(cost_usd, 0), COALESCE(status, 'ok')
    FROM public.dash_apigw_usage
  UNION ALL
  -- embed widget chat (external visitors) — model = real engine model
  SELECT 'embed'::text, ts, COALESCE(external_user, 'anon'), NULL::text,
         COALESCE(NULLIF(engine_model, ''), 'google/gemini-3-flash-preview'),
         COALESCE(tokens_in, 0), COALESCE(tokens_out, 0), COALESCE(cost_usd, 0),
         CASE WHEN success THEN 'ok' ELSE 'error' END
    FROM public.dash_embed_calls;

COMMENT ON VIEW public.v_usage_unified IS
  'Normalized usage/cost spine. platform/training from dash_llm_costs; gateway chat+embeddings from dash_apigw_usage; embed widget from dash_embed_calls. model column = real engine_model (LLM id) for cost breakdown.';
