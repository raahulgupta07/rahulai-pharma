ALTER TABLE public.dash_hypotheses
  ADD COLUMN IF NOT EXISTS parent_hypothesis_id INTEGER
    REFERENCES public.dash_hypotheses(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS lineage_depth INTEGER DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_hypotheses_parent
  ON public.dash_hypotheses(parent_hypothesis_id)
  WHERE parent_hypothesis_id IS NOT NULL;

COMMENT ON COLUMN public.dash_hypotheses.parent_hypothesis_id
  IS 'Hypothesis that spawned this one (cycle_followup chain).';
COMMENT ON COLUMN public.dash_hypotheses.lineage_depth
  IS 'Generations from root (root=0, children=1, grandchildren=2...).';
