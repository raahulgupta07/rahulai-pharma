-- 025_embed_visibility_binding.sql
-- Wire embed widgets into the visibility policy engine. Each embed can be
-- bound to a fixed scope/intent/role so all sessions inherit those values
-- regardless of what the host site sends.
-- Idempotent.

ALTER TABLE public.dash_agent_embeds
  ADD COLUMN IF NOT EXISTS bound_scope_id TEXT,
  ADD COLUMN IF NOT EXISTS bound_intent   TEXT DEFAULT 'public',
  ADD COLUMN IF NOT EXISTS bound_role     TEXT;

-- WHY: existing rows had no bound_intent default; make sure they read 'public'
UPDATE public.dash_agent_embeds
   SET bound_intent = 'public'
 WHERE bound_intent IS NULL;
