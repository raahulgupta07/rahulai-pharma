-- 189_brain_intelligence.sql — Insight compilation (#1) + fact freshness (#4).
--
-- Adds a REVIEW GATE + freshness tracking to the durable knowledge stores so a
-- background daemon can DISTILL insights from query history + live data and write
-- them as `status='pending'` (Intern Rule — never auto-trusted into chat until an
-- admin approves), and so stale facts can be flagged for re-verification.
--
-- dash_memories already carries citation_count / last_cited_at / confidence_score
-- (mig 002); it only lacked a status gate + created_by, added here.
-- Idempotent (ADD COLUMN IF NOT EXISTS); existing rows default to 'active' so
-- nothing currently injected disappears.

-- ── company brain: review gate + freshness ──────────────────────────────────
ALTER TABLE public.dash_company_brain
  ADD COLUMN IF NOT EXISTS status         TEXT    NOT NULL DEFAULT 'active',  -- active | pending | rejected
  ADD COLUMN IF NOT EXISTS source         TEXT             DEFAULT 'human',   -- human | insight_daemon | distilled
  ADD COLUMN IF NOT EXISTS citation_count INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS last_cited_at  TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS needs_reverify BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS dash_company_brain_status
  ON public.dash_company_brain (project_slug, status);

-- ── memories: review gate + author ──────────────────────────────────────────
ALTER TABLE public.dash_memories
  ADD COLUMN IF NOT EXISTS status     TEXT DEFAULT 'active',  -- active | pending | rejected
  ADD COLUMN IF NOT EXISTS created_by TEXT;

CREATE INDEX IF NOT EXISTS dash_memories_status
  ON public.dash_memories (project_slug, status);

-- ── insight audit trail (what the daemon proposed + the numbers behind it) ───
-- One row per distilled insight cycle output, so an admin can review the
-- supporting figures before approving the brain entry. Pure analytics; the
-- authoritative copy lives in dash_company_brain (status='pending').
CREATE TABLE IF NOT EXISTS public.dash_insights (
  id            BIGSERIAL PRIMARY KEY,
  project_slug  TEXT NOT NULL,
  kind          TEXT NOT NULL,              -- blind_spot | concentration | stale_fact | demand_theme | coverage
  title         TEXT NOT NULL,
  detail        TEXT NOT NULL,
  evidence      JSONB DEFAULT '{}',         -- the numbers/SQL behind it (audit)
  brain_id      INTEGER,                    -- dash_company_brain row it created (NULL = not promoted)
  status        TEXT NOT NULL DEFAULT 'pending',  -- pending | approved | rejected
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS dash_insights_slug
  ON public.dash_insights (project_slug, status, created_at DESC);
