-- 175_usage_deep.sql — deep usage insights: traces-backed cost spine + security
-- events + chat-body logging + budget. Idempotent + fail-soft.
--
-- KEY CHANGE: platform/api_key/training cost now comes from public.dash_traces
-- ROOT spans (the REAL cost ledger — dash_llm_costs is empty in this deploy).
-- Chat cost/tokens/latency live on root spans (parent_id IS NULL); channel/actor/
-- store/model are stamped into meta by app/projects.py going forward.

-- apigw: per-call latency (embeddings + chat metering)
ALTER TABLE public.dash_apigw_usage
  ADD COLUMN IF NOT EXISTS latency_ms INTEGER;

-- Security / guardrail events (store-lock leaks, blocked, rate-limited, auth-fail)
CREATE TABLE IF NOT EXISTS public.dash_security_events (
  id              BIGSERIAL PRIMARY KEY,
  ts              TIMESTAMPTZ NOT NULL DEFAULT now(),
  kind            TEXT NOT NULL,            -- leak | blocked | rate_limited | auth_fail | bad_key
  severity        TEXT NOT NULL DEFAULT 'WARN',
  service_account TEXT,
  key_id          INTEGER,
  store_id        TEXT,
  detail          TEXT,
  meta            JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_sec_events_ts   ON public.dash_security_events(ts DESC);
CREATE INDEX IF NOT EXISTS idx_sec_events_kind ON public.dash_security_events(kind, ts DESC);

-- Gateway chat bodies (opt-in, env APIGW_LOG_BODIES; privacy-sensitive)
CREATE TABLE IF NOT EXISTS public.dash_apigw_messages (
  id              BIGSERIAL PRIMARY KEY,
  ts              TIMESTAMPTZ NOT NULL DEFAULT now(),
  session_id      TEXT,
  key_id          INTEGER,
  service_account TEXT,
  store_id        TEXT,
  role            TEXT,                     -- user | assistant
  content         TEXT,
  masked          BOOLEAN NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS idx_apigw_msg_key_ts ON public.dash_apigw_messages(key_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_apigw_msg_sess   ON public.dash_apigw_messages(session_id, ts);

-- Budget singleton (daily/monthly USD targets for alerts)
CREATE TABLE IF NOT EXISTS public.dash_usage_budget (
  id           INTEGER PRIMARY KEY DEFAULT 1,
  daily_usd    NUMERIC(12,2) NOT NULL DEFAULT 0,
  monthly_usd  NUMERIC(12,2) NOT NULL DEFAULT 0,
  updated_at   TIMESTAMPTZ DEFAULT now(),
  CHECK (id = 1)
);
INSERT INTO public.dash_usage_budget (id) VALUES (1) ON CONFLICT DO NOTHING;

-- Rewrite the unified spine: traces for chat/training, apigw for embeddings, embed widget.
-- DROP first — column list/order changed (added latency_ms), so REPLACE can't alter it.
DROP VIEW IF EXISTS public.v_usage_unified;
CREATE VIEW public.v_usage_unified AS
  -- platform + api_key chat (split by meta.channel) from trace ROOT spans
  SELECT CASE WHEN meta->>'channel' = 'api' THEN 'api_key' ELSE 'platform' END AS src,
         started_at AS ts,
         COALESCE(NULLIF(meta->>'actor',''), 'system')   AS actor,
         meta->>'store_id'                                AS store_id,
         COALESCE(NULLIF(meta->>'model',''), '(none)')    AS model,
         COALESCE(tokens, 0)                              AS tokens_in,
         0                                                AS tokens_out,
         COALESCE(cost_usd, 0)                            AS cost_usd,
         COALESCE(duration_ms, 0)                         AS latency_ms,
         CASE WHEN status = 'error' THEN 'error' ELSE 'ok' END AS status
    FROM public.dash_traces
   WHERE kind = 'chat' AND parent_id IS NULL
  UNION ALL
  -- training / learning runs
  SELECT 'training', started_at, 'system', NULL,
         COALESCE(NULLIF(meta->>'model',''), '(none)'),
         COALESCE(tokens, 0), 0, COALESCE(cost_usd, 0), COALESCE(duration_ms, 0),
         CASE WHEN status = 'error' THEN 'error' ELSE 'ok' END
    FROM public.dash_traces
   WHERE kind IN ('training', 'learning') AND parent_id IS NULL
  UNION ALL
  -- embeddings (metered through the gateway)
  SELECT 'embedding', ts, COALESCE(service_account, 'unknown'), store_id, model,
         COALESCE(prompt_tokens, 0), COALESCE(completion_tokens, 0),
         COALESCE(cost_usd, 0), COALESCE(latency_ms, 0), COALESCE(status, 'ok')
    FROM public.dash_apigw_usage
   WHERE request_type = 'embedding'
  UNION ALL
  -- embed widget chat (external visitors)
  SELECT 'embed', ts, COALESCE(external_user, 'anon'), NULL, NULL,
         COALESCE(tokens_in, 0), COALESCE(tokens_out, 0), COALESCE(cost_usd, 0),
         COALESCE(latency_ms, 0),
         CASE WHEN success THEN 'ok' ELSE 'error' END
    FROM public.dash_embed_calls;

COMMENT ON VIEW public.v_usage_unified IS
  'Normalized usage/cost spine. Chat+training cost/tokens/latency from dash_traces ROOT spans (real ledger); embeddings from dash_apigw_usage; embed widget from dash_embed_calls. cron excluded.';
