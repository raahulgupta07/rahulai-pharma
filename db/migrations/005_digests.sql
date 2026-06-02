CREATE TABLE IF NOT EXISTS public.dash_digests (
  id SERIAL PRIMARY KEY,
  project_slug TEXT,
  cycle_num INTEGER,
  run_id INTEGER REFERENCES public.dash_self_learning_runs(id) ON DELETE SET NULL,
  summary TEXT NOT NULL,
  highlights JSONB DEFAULT '[]',
  hypotheses_count INTEGER DEFAULT 0,
  verified_count INTEGER DEFAULT 0,
  cost_usd NUMERIC(10,4) DEFAULT 0,
  agent_iq NUMERIC(10,2),
  notified_via JSONB DEFAULT '[]',
  created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_digests_project_created
  ON public.dash_digests(project_slug, created_at DESC);
