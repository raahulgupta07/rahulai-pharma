-- 188_query_bank.sql — [P1] Continuous query learning: capture the SQL the agent
-- writes in LIVE chat, so the system learns from real questions (not just training).
-- Plan: docs/plans/continuous_query_learning.md.
--
-- We EXTEND the existing public.dash_query_patterns (already a Q->SQL store, has
-- source/uses/last_used/version/parent_id) rather than add a new table. Chat-captured
-- rows are source='chat', status='pending' (review gate). Question embeddings reuse
-- dash.dash_vectors namespace='qbank' (HNSW cosine, source_id = pattern id).
--
-- P1 = CAPTURE + SHADOW only: nothing is served from these rows yet. The shadow log
-- records "this live question WOULD have matched bank row X (sim, status)" so we can
-- measure repeat-rate before ever flipping serve on.

-- ── extend dash_query_patterns ───────────────────────────────────────────────
ALTER TABLE public.dash_query_patterns
  ADD COLUMN IF NOT EXISTS status          TEXT    NOT NULL DEFAULT 'proven', -- pending | candidate | proven | demoted
  ADD COLUMN IF NOT EXISTS schema_hash     TEXT,                              -- cols-only hash of source tables (schema_guard)
  ADD COLUMN IF NOT EXISTS rows_returned   INTEGER,                           -- rows the captured SQL returned
  ADD COLUMN IF NOT EXISTS last_latency_ms INTEGER,                           -- exec latency of the captured run
  ADD COLUMN IF NOT EXISTS success         BOOLEAN NOT NULL DEFAULT TRUE,     -- did the SQL execute clean
  ADD COLUMN IF NOT EXISTS question_norm   TEXT;                              -- normalized question for dedupe

-- Pre-existing training rows are trusted as proven (back-compat default already 'proven').
-- Normalize existing questions for dedupe (idempotent best-effort).
UPDATE public.dash_query_patterns
   SET question_norm = lower(regexp_replace(COALESCE(question, ''), '\s+', ' ', 'g'))
 WHERE question_norm IS NULL;

-- Remove any pre-existing duplicate (slug, question_norm) rows BEFORE the unique
-- index is built (training seed may have repeats) — keep the highest id (newest).
DELETE FROM public.dash_query_patterns a
 USING public.dash_query_patterns b
 WHERE a.question_norm IS NOT NULL
   AND a.project_slug = b.project_slug
   AND a.question_norm = b.question_norm
   AND a.id < b.id;

-- Dedupe target for capture upserts (per slug + normalized question).
CREATE UNIQUE INDEX IF NOT EXISTS dash_query_patterns_norm_uq
  ON public.dash_query_patterns (project_slug, question_norm)
  WHERE question_norm IS NOT NULL;

CREATE INDEX IF NOT EXISTS dash_query_patterns_status
  ON public.dash_query_patterns (project_slug, source, status);

-- ── shadow log (P1 measurement; dropped once serve lands) ────────────────────
-- One row per live question that fell through to the agent, recording whether it
-- WOULD have hit the bank. Read-only analytics; never serves anything.
CREATE TABLE IF NOT EXISTS public.dash_query_bank_shadow (
  id            BIGSERIAL PRIMARY KEY,
  project_slug  TEXT NOT NULL,
  question      TEXT NOT NULL,
  matched_id    INTEGER,                  -- dash_query_patterns.id of the nearest bank row (NULL = no match)
  sim           NUMERIC,                  -- cosine similarity to the nearest bank row
  matched_status TEXT,                    -- status of the matched row
  would_serve   BOOLEAN NOT NULL DEFAULT FALSE, -- sim >= serve threshold AND proven AND schema-ok
  schema_ok     BOOLEAN,                  -- did the matched row pass the schema-drift guard
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS dash_query_bank_shadow_slug
  ON public.dash_query_bank_shadow (project_slug, created_at DESC);
