-- Per-call LLM cost ledger. Source of truth for daily/monthly cost rollups
-- across ALL LLM activity (training, chat, vision, eval, etc.).
-- Idempotent.
CREATE TABLE IF NOT EXISTS public.dash_llm_costs (
    id           BIGSERIAL PRIMARY KEY,
    project_slug TEXT,
    ts           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    task         TEXT,
    model        TEXT,
    cost_usd     NUMERIC(12,6) NOT NULL DEFAULT 0,
    tokens_in    INTEGER       NOT NULL DEFAULT 0,
    tokens_out   INTEGER       NOT NULL DEFAULT 0,
    ok           BOOLEAN       NOT NULL DEFAULT TRUE,
    meta         JSONB         NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_dash_llm_costs_slug_ts
    ON public.dash_llm_costs (project_slug, ts DESC);

CREATE INDEX IF NOT EXISTS idx_dash_llm_costs_ts
    ON public.dash_llm_costs (ts DESC);

COMMENT ON TABLE public.dash_llm_costs IS
    'Per-call LLM cost ledger; primary source for daily/monthly cost rollups.';
