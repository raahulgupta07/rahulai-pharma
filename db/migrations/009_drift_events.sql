CREATE TABLE IF NOT EXISTS public.dash_drift_events (
  id SERIAL PRIMARY KEY,
  project_slug TEXT NOT NULL,
  source_id INTEGER REFERENCES public.dash_data_sources(id) ON DELETE CASCADE,
  drift_type TEXT NOT NULL,        -- 'schema'|'ndv'|'row_count'|'distribution'|'watermark'|'pii_change'
  severity TEXT NOT NULL,           -- 'low'|'med'|'high'|'critical'
  table_name TEXT,
  column_name TEXT,
  details JSONB DEFAULT '{}',       -- {old, new, pct_change, etc.}
  status TEXT NOT NULL DEFAULT 'open',  -- 'open'|'acknowledged'|'dismissed'|'retrain_triggered'
  detected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  acknowledged_at TIMESTAMPTZ,
  acknowledged_by INTEGER,
  metadata JSONB DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_drift_events_project_status
  ON public.dash_drift_events(project_slug, status, detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_drift_events_source
  ON public.dash_drift_events(source_id);
CREATE INDEX IF NOT EXISTS idx_drift_events_open
  ON public.dash_drift_events(detected_at DESC)
  WHERE status = 'open';

COMMENT ON TABLE public.dash_drift_events IS
  'Per-source drift events from drift_detector. project_slug-scoped for tenant isolation.';
