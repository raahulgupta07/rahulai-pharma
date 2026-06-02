-- Phase 7: SSE audit table — fail-soft, append-only.
-- Captures per-event emit telemetry from dash/utils/sse.py so the
-- super-admin can find broken streams (sessions that emitted ReasoningStep
-- but never TeamRunContent) and event-volume / error rollups.

CREATE TABLE IF NOT EXISTS public.dash_sse_audit (
  id BIGSERIAL PRIMARY KEY,
  session_id TEXT,
  event_name TEXT NOT NULL,
  ts TIMESTAMPTZ NOT NULL DEFAULT now(),
  bytes_emitted INTEGER,
  error TEXT,
  project_slug TEXT
);

CREATE INDEX IF NOT EXISTS idx_dash_sse_audit_session
  ON public.dash_sse_audit (session_id, ts DESC);

CREATE INDEX IF NOT EXISTS idx_dash_sse_audit_event_ts
  ON public.dash_sse_audit (event_name, ts DESC);

CREATE INDEX IF NOT EXISTS idx_dash_sse_audit_missing
  ON public.dash_sse_audit (session_id) WHERE event_name = 'TeamRunContent';
