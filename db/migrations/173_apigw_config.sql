-- 173_apigw_config.sql — live-editable API gateway runtime config (singleton).
-- One row (id=1) holding the per-minute rate cap, editable from the super-admin
-- "API Gateway" admin page. The gateway reads it via _effective_cap() with a
-- short TTL cache, falling back to the API_GW_RATE_PER_MIN env on any miss.
-- Idempotent.

CREATE TABLE IF NOT EXISTS public.dash_apigw_config (
  id            INT PRIMARY KEY DEFAULT 1,
  rate_per_min  INT NOT NULL DEFAULT 60,
  updated_at    TIMESTAMPTZ DEFAULT now(),
  CHECK (id = 1)
);

INSERT INTO public.dash_apigw_config (id) VALUES (1)
  ON CONFLICT DO NOTHING;
