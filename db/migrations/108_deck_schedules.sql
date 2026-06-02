-- 108_deck_schedules.sql
-- Phase 7: Deck distribution scheduling (idempotent).
CREATE TABLE IF NOT EXISTS public.dash_deck_schedules (
  id SERIAL PRIMARY KEY,
  project_slug TEXT NOT NULL,
  presentation_id INT NOT NULL,
  name TEXT NOT NULL,
  cron TEXT NOT NULL,
  recipients JSONB NOT NULL,
  channel TEXT NOT NULL DEFAULT 'email',
  format TEXT NOT NULL DEFAULT 'pptx',
  enabled BOOLEAN DEFAULT TRUE,
  last_run_at TIMESTAMP,
  last_status TEXT,
  last_error TEXT,
  created_by INT,
  created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_deck_schedules_slug
  ON public.dash_deck_schedules (project_slug, enabled);
