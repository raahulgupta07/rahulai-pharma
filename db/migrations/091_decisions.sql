-- Decision log: captures user-saved SO_WHAT actions from chat (McKinsey-style decision diary).
CREATE TABLE IF NOT EXISTS public.dash_decisions (
    id            BIGSERIAL PRIMARY KEY,
    project_slug  TEXT NOT NULL,
    user_id       INTEGER,
    chat_msg_id   TEXT,
    action        TEXT NOT NULL,
    owner         TEXT,
    effort        TEXT,
    risk          TEXT,
    status        TEXT NOT NULL DEFAULT 'open',  -- open|in_progress|done|dismissed
    source_excerpt TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_dash_decisions_slug_created
    ON public.dash_decisions(project_slug, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_dash_decisions_user
    ON public.dash_decisions(user_id, created_at DESC);
