-- 015_skill_refinery.sql — SkillRefinery: per-tool utility tracking + patches.
-- Idempotent: re-runs are safe.

-- 1. Per-invocation telemetry.
CREATE TABLE IF NOT EXISTS public.dash_tool_utility_scores (
    id              BIGSERIAL PRIMARY KEY,
    tool_name       TEXT NOT NULL,
    agent           TEXT,
    project_slug    TEXT,
    user_id         INTEGER,
    args_hash       TEXT,
    success         BOOLEAN NOT NULL,
    latency_ms      INTEGER,
    error_class     TEXT,
    error_message   TEXT,
    feedback        SMALLINT,            -- -1, 0, +1 (down/none/up)
    retry_count     SMALLINT DEFAULT 0,
    ts              TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tool_utility_tool_proj_ts
    ON public.dash_tool_utility_scores(tool_name, project_slug, ts DESC);

CREATE INDEX IF NOT EXISTS idx_tool_utility_proj_ts
    ON public.dash_tool_utility_scores(project_slug, ts DESC);

CREATE INDEX IF NOT EXISTS idx_tool_utility_success
    ON public.dash_tool_utility_scores(tool_name, success);

COMMENT ON TABLE public.dash_tool_utility_scores IS
    'SkillRefinery: per-call telemetry feeding nightly utility scoring + patch generation';

-- 2. LLM-proposed patches (description + default_args overrides).
CREATE TABLE IF NOT EXISTS public.dash_tool_patches (
    id                  BIGSERIAL PRIMARY KEY,
    tool_name           TEXT NOT NULL,
    project_slug        TEXT,                    -- NULL = global; else per-project override
    version             INTEGER NOT NULL DEFAULT 1,
    old_description     TEXT,
    new_description     TEXT,
    default_args        JSONB,                   -- merged into tool kwargs at call time
    reason              TEXT,                    -- LLM rationale
    failure_samples     JSONB,                   -- top failures used to derive patch
    score_before        NUMERIC(5,2),
    score_after         NUMERIC(5,2),
    shadow_pass_rate    NUMERIC(5,2),            -- Phase 6
    applied             BOOLEAN DEFAULT FALSE,
    applied_at          TIMESTAMPTZ,
    reverted            BOOLEAN DEFAULT FALSE,
    reverted_at         TIMESTAMPTZ,
    revert_reason       TEXT,
    source              TEXT DEFAULT 'auto',     -- 'auto' | 'manual' | 'transferred'
    created_by          INTEGER,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tool_patches_active
    ON public.dash_tool_patches(tool_name, project_slug, applied)
    WHERE applied = TRUE AND reverted = FALSE;

CREATE INDEX IF NOT EXISTS idx_tool_patches_tool_ver
    ON public.dash_tool_patches(tool_name, project_slug, version DESC);

COMMENT ON TABLE public.dash_tool_patches IS
    'SkillRefinery: versioned tool description/arg patches. project_slug=NULL means global override.';
