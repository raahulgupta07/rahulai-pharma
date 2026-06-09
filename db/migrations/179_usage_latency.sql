-- 179: expose latency_ms on v_usage_unified
-- LIVE view = mig 174 shape (175 never executed). It lacked latency_ms, so
-- /performance + Overview activity-feed + /entity activity queries silently
-- died in _rows() and rendered empty/zero. Append latency_ms (apigw+embed have
-- it; llm_costs legs NULL). Append-only keeps CREATE OR REPLACE valid.
CREATE OR REPLACE VIEW public.v_usage_unified AS
  SELECT 'platform'::text AS src,
     dash_llm_costs.ts,
     COALESCE(dash_llm_costs.actor, 'system'::text) AS actor,
     NULL::text AS store_id,
     dash_llm_costs.model,
     COALESCE(dash_llm_costs.tokens_in, 0) AS tokens_in,
     COALESCE(dash_llm_costs.tokens_out, 0) AS tokens_out,
     COALESCE(dash_llm_costs.cost_usd, 0::numeric) AS cost_usd,
         CASE WHEN dash_llm_costs.ok THEN 'ok'::text ELSE 'error'::text END AS status,
     NULL::integer AS latency_ms
    FROM dash_llm_costs
   WHERE COALESCE(dash_llm_costs.task, ''::text) !~~ 'train%'::text
 UNION ALL
  SELECT 'training'::text AS src,
     dash_llm_costs.ts,
     'system'::text AS actor,
     NULL::text AS store_id,
     dash_llm_costs.model,
     COALESCE(dash_llm_costs.tokens_in, 0) AS tokens_in,
     COALESCE(dash_llm_costs.tokens_out, 0) AS tokens_out,
     COALESCE(dash_llm_costs.cost_usd, 0::numeric) AS cost_usd,
         CASE WHEN dash_llm_costs.ok THEN 'ok'::text ELSE 'error'::text END AS status,
     NULL::integer AS latency_ms
    FROM dash_llm_costs
   WHERE COALESCE(dash_llm_costs.task, ''::text) ~~ 'train%'::text
 UNION ALL
  SELECT
         CASE WHEN dash_apigw_usage.request_type = 'embedding'::text THEN 'embedding'::text
              ELSE 'api_key'::text END AS src,
     dash_apigw_usage.ts,
     COALESCE(dash_apigw_usage.service_account, 'unknown'::text) AS actor,
     dash_apigw_usage.store_id,
     COALESCE(NULLIF(dash_apigw_usage.engine_model, ''::text), dash_apigw_usage.model) AS model,
     COALESCE(dash_apigw_usage.prompt_tokens, 0) AS tokens_in,
     COALESCE(dash_apigw_usage.completion_tokens, 0) AS tokens_out,
     COALESCE(dash_apigw_usage.cost_usd, 0::numeric) AS cost_usd,
     COALESCE(dash_apigw_usage.status, 'ok'::text) AS status,
     dash_apigw_usage.latency_ms
    FROM dash_apigw_usage
 UNION ALL
  SELECT 'embed'::text AS src,
     dash_embed_calls.ts,
     COALESCE(dash_embed_calls.external_user, 'anon'::text) AS actor,
     NULL::text AS store_id,
     COALESCE(NULLIF(dash_embed_calls.engine_model, ''::text), 'google/gemini-3-flash-preview'::text) AS model,
     COALESCE(dash_embed_calls.tokens_in, 0) AS tokens_in,
     COALESCE(dash_embed_calls.tokens_out, 0) AS tokens_out,
     COALESCE(dash_embed_calls.cost_usd, 0::numeric) AS cost_usd,
         CASE WHEN dash_embed_calls.success THEN 'ok'::text ELSE 'error'::text END AS status,
     dash_embed_calls.latency_ms
    FROM dash_embed_calls;
