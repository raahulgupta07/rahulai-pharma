-- Add federation-specific columns to dash_audit_log
ALTER TABLE public.dash_audit_log
  ADD COLUMN IF NOT EXISTS project_slug TEXT,
  ADD COLUMN IF NOT EXISTS target TEXT,
  ADD COLUMN IF NOT EXISTS sources_used JSONB,
  ADD COLUMN IF NOT EXISTS row_count INTEGER,
  ADD COLUMN IF NOT EXISTS latency_ms INTEGER,
  ADD COLUMN IF NOT EXISTS cost_usd NUMERIC(10,6);

CREATE INDEX IF NOT EXISTS idx_audit_log_action_created
  ON public.dash_audit_log(action, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_log_project_created
  ON public.dash_audit_log(project_slug, created_at DESC);

-- Federation circuit breaker state
CREATE TABLE IF NOT EXISTS public.dash_federation_circuit (
  project_slug TEXT PRIMARY KEY,
  consecutive_failures INTEGER DEFAULT 0,
  open_until TIMESTAMPTZ,
  last_error TEXT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE public.dash_federation_circuit IS
  'Federation circuit breaker — opens after N consecutive failures, blocks calls.';
