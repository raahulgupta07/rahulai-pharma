-- 096_pptxgenjs.sql
-- Phase 3 of Deep Deck: Node-based pptxgenjs renderer.
-- Adds render engine selector, rendered file path, and the LLM-emitted spec.
-- All additive + idempotent.

ALTER TABLE public.dash_presentations
  ADD COLUMN IF NOT EXISTS render_engine TEXT DEFAULT 'python-pptx',
  ADD COLUMN IF NOT EXISTS rendered_pptx_path TEXT,
  ADD COLUMN IF NOT EXISTS pptxgenjs_spec JSONB;

CREATE INDEX IF NOT EXISTS idx_pres_engine ON public.dash_presentations(render_engine);
