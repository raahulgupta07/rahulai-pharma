-- 184_embed_starter_questions.sql
-- Per-widget starter (initial suggestion) questions for the embed chat widget.
-- Falls back to the global `embed_default_starters` setting when empty.
-- Idempotent.

ALTER TABLE public.dash_agent_embeds
    ADD COLUMN IF NOT EXISTS starter_questions JSONB DEFAULT '[]'::jsonb;

-- The widget greeting now resolves through a fallback chain in
-- GET /api/embed/config/{id}:  per-widget welcome_msg ?? brand ??
-- global embed_default_welcome (Burmese) ?? hard fallback. An empty/NULL
-- per-widget value lets the Burmese default apply. The old column DEFAULT
-- ('Hi! How can I help?') baked the English string into every row so the
-- fallback never fired. Drop the column default (new rows inherit the global
-- default) and null-out rows that still carry the old hard English default so
-- existing widgets pick up the configurable Burmese greeting.
ALTER TABLE public.dash_agent_embeds ALTER COLUMN welcome_msg DROP DEFAULT;

UPDATE public.dash_agent_embeds
   SET welcome_msg = NULL
 WHERE welcome_msg = 'Hi! How can I help?';
