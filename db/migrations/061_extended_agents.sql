-- 061_extended_agents.sql
-- Adds:
--   * public.dash_hitl_requests   — pause/resume HITL records (separate from dash.dash_hitl_pending)
--   * public.dash_workflow_runs_v2 — generic workflow run store (idempotent if exists elsewhere)
--   * agent registry "category" column (creates registry if missing)
--
-- Schema-qualified to public. Idempotent. Migration runner auto-tracks.

-- ── HITL requests (pause/resume on confirm) ────────────────────────────
CREATE TABLE IF NOT EXISTS public.dash_hitl_requests (
  id BIGSERIAL PRIMARY KEY,
  request_id TEXT UNIQUE NOT NULL,            -- hitl_<8hex>
  project_slug TEXT NOT NULL,
  agent_name TEXT NOT NULL,
  operation TEXT NOT NULL,                     -- e.g. 'DELETE FROM customers'
  details JSONB NOT NULL DEFAULT '{}'::jsonb,
  state TEXT NOT NULL DEFAULT 'pending',       -- pending | approved | rejected | expired
  requested_by TEXT NOT NULL,
  responded_by TEXT,
  response_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ NOT NULL DEFAULT (now() + INTERVAL '1 hour')
);

CREATE INDEX IF NOT EXISTS idx_hitl_state ON public.dash_hitl_requests(project_slug, state);
CREATE INDEX IF NOT EXISTS idx_hitl_request_id ON public.dash_hitl_requests(request_id);
CREATE INDEX IF NOT EXISTS idx_hitl_created ON public.dash_hitl_requests(created_at DESC);

-- ── Workflow runs (idempotent, generic) ────────────────────────────────
CREATE TABLE IF NOT EXISTS public.dash_workflow_runs_v2 (
  id BIGSERIAL PRIMARY KEY,
  run_id TEXT UNIQUE NOT NULL,                 -- wfr2_<8hex>
  workflow_name TEXT NOT NULL,
  project_slug TEXT,
  triggered_by TEXT,
  status TEXT NOT NULL DEFAULT 'pending',      -- pending | running | done | failed | cancelled
  input_args JSONB NOT NULL DEFAULT '{}'::jsonb,
  result JSONB,
  error TEXT,
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_wfrunv2_workflow ON public.dash_workflow_runs_v2(workflow_name, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_wfrunv2_status   ON public.dash_workflow_runs_v2(status);
CREATE INDEX IF NOT EXISTS idx_wfrunv2_run_id   ON public.dash_workflow_runs_v2(run_id);

-- ── Agent registry + category ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.dash_agent_registry (
  id BIGSERIAL PRIMARY KEY,
  agent_name TEXT UNIQUE NOT NULL,
  description TEXT,
  tags JSONB NOT NULL DEFAULT '[]'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE public.dash_agent_registry
  ADD COLUMN IF NOT EXISTS category TEXT;

CREATE INDEX IF NOT EXISTS idx_agent_registry_category ON public.dash_agent_registry(category);
