-- 177_embed_log_bodies.sql — optional question/answer text capture for embed calls.
-- Columns are nullable + only WRITTEN when EMBED_LOG_BODIES=1 (privacy + size default OFF).
-- The usage dashboard (app/usage_api.py) auto-detects these columns: present + populated
-- => messages_enabled=true and the per-call QUESTION/ANSWER panels fill in.
-- Idempotent.

ALTER TABLE public.dash_embed_calls
  ADD COLUMN IF NOT EXISTS message_text  TEXT,
  ADD COLUMN IF NOT EXISTS response_text TEXT;
