-- Embed HMAC secret: add encrypted-at-rest column.
-- Plaintext `secret_key` column is kept for legacy rows; new rows write only
-- to `secret_key_encrypted` (Fernet-encrypted via dash/embed/secret_storage.py).
-- Auth flow (dash/embed/auth.py) tries encrypted first, falls back to plaintext.

SELECT pg_advisory_lock(72157440);

ALTER TABLE public.dash_agent_embeds
  ADD COLUMN IF NOT EXISTS secret_key_encrypted TEXT;

SELECT pg_advisory_unlock(72157440);
