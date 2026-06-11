-- 187_autonomy_state.sql — token-frugal autonomy heartbeat core.
-- Two tables backing the FREE detection / PAID-rare-thinking heartbeat loop
-- (dash/cron/heartbeat.py). A quiet tick reads SQL signals + sleeps with ZERO
-- tokens and writes NO journal row; only a TRIPPED signal (T3) journals an
-- intent row.
--
--   dash_autonomy_state   — last-seen signal snapshot per project (one row).
--   dash_autonomy_journal — append-only log of T3 intents / budget events,
--                           with a per-row token cost for the daily budget cap.

CREATE TABLE IF NOT EXISTS public.dash_autonomy_state (
  project_slug TEXT PRIMARY KEY,
  signals      JSONB NOT NULL DEFAULT '{}'::jsonb,
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.dash_autonomy_journal (
  id           BIGSERIAL PRIMARY KEY,
  project_slug TEXT,
  ts           TIMESTAMPTZ NOT NULL DEFAULT now(),
  tier         TEXT,
  signal       TEXT,
  action       TEXT,
  detail       JSONB,
  tokens       INT NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS dash_autonomy_journal_slug_ts
  ON public.dash_autonomy_journal (project_slug, ts DESC);
