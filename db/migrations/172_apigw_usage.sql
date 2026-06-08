-- 172_apigw_usage.sql — OpenAI-compatible API gateway usage metering.
-- Every /api/v1/chat/completions completion (blocking + streaming) logs one row
-- here so we can bill / rate-audit external service keys. Fail-soft: the gateway
-- never blocks a response on a metering write. Idempotent.

CREATE TABLE IF NOT EXISTS public.dash_apigw_usage (
  id                BIGSERIAL PRIMARY KEY,
  key_id            INT,                 -- dash_users.id the API key binds to
  service_account   TEXT,                -- username of the key owner
  store_id          TEXT,                -- bound store (site_code/store_id)
  scope_mode        TEXT,                -- 'store' | 'global'
  model             TEXT,
  prompt_tokens     INT,
  completion_tokens INT,
  total_tokens      INT,
  streamed          BOOLEAN,
  ts                TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_apigw_usage_key_ts
  ON public.dash_apigw_usage(key_id, ts DESC);
