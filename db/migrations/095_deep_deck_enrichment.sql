-- 095_deep_deck_enrichment.sql
-- Phase 2 of Deep Deck: audience variants, critic loop, TTS narration,
-- live-data slides. All additive + idempotent.

-- Extend dash_presentations with new metadata
ALTER TABLE public.dash_presentations
    ADD COLUMN IF NOT EXISTS audience TEXT;
ALTER TABLE public.dash_presentations
    ADD COLUMN IF NOT EXISTS parent_id BIGINT;
ALTER TABLE public.dash_presentations
    ADD COLUMN IF NOT EXISTS critique_pass INT DEFAULT 0;
ALTER TABLE public.dash_presentations
    ADD COLUMN IF NOT EXISTS hero_image_url TEXT;
ALTER TABLE public.dash_presentations
    ADD COLUMN IF NOT EXISTS narration_status TEXT;   -- pending | done | skipped | failed

CREATE INDEX IF NOT EXISTS idx_pres_parent ON public.dash_presentations(parent_id);
CREATE INDEX IF NOT EXISTS idx_pres_audience ON public.dash_presentations(audience);

-- F5: TTS narration audio per slide
CREATE TABLE IF NOT EXISTS dash.dash_slide_narration (
    id           BIGSERIAL PRIMARY KEY,
    pres_id      BIGINT NOT NULL,
    slide_idx    INT NOT NULL,
    voice        TEXT,                -- e.g. 'alloy', 'nova', 'shimmer'
    audio_path   TEXT,                -- relative to knowledge/
    audio_url    TEXT,                -- public-served path
    duration_ms  INT,
    text_hash    TEXT,                -- sha256 of speaker_notes (dedupe / cache)
    cost_usd     NUMERIC(8,4),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_slide_narration_pres ON dash.dash_slide_narration(pres_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_slide_narration_pres_slide
    ON dash.dash_slide_narration(pres_id, slide_idx);

-- F1: live-data slides (SQL re-runs on viewer open)
CREATE TABLE IF NOT EXISTS dash.dash_slide_live_data (
    id              BIGSERIAL PRIMARY KEY,
    pres_id         BIGINT NOT NULL,
    slide_idx       INT NOT NULL,
    sql_text        TEXT NOT NULL,
    refresh_mode    TEXT DEFAULT 'on_open', -- on_open | hourly | manual
    last_run_at     TIMESTAMPTZ,
    cached_result   JSONB,
    cached_columns  JSONB,
    error_text      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_slide_live_pres ON dash.dash_slide_live_data(pres_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_slide_live_pres_slide
    ON dash.dash_slide_live_data(pres_id, slide_idx);

-- F4: Slide critic scores per slide per pres
CREATE TABLE IF NOT EXISTS dash.dash_slide_critique (
    id              BIGSERIAL PRIMARY KEY,
    pres_id         BIGINT NOT NULL,
    slide_idx       INT NOT NULL,
    pass_num        INT NOT NULL,          -- 1 or 2
    score           NUMERIC(3,2),          -- 1.00-5.00
    weaknesses      JSONB,                 -- ["vague title", "no source"]
    suggested_fix   TEXT,
    accepted        BOOLEAN DEFAULT FALSE, -- did we apply the fix?
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_slide_critique_pres ON dash.dash_slide_critique(pres_id);
