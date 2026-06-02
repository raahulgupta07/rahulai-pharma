-- 062_embed_per_agent.sql — per-agent default-enabled embeds + theme config.
-- Idempotent. Extends 019_agent_embeds.sql.

ALTER TABLE public.dash_agent_embeds
    ADD COLUMN IF NOT EXISTS agent_id          TEXT,
    ADD COLUMN IF NOT EXISTS auto_provisioned  BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS status            TEXT DEFAULT 'live',  -- live|draft|disabled
    ADD COLUMN IF NOT EXISTS primary_color     TEXT DEFAULT '#1a2b4a',
    ADD COLUMN IF NOT EXISTS logo_url          TEXT,
    ADD COLUMN IF NOT EXISTS welcome_msg       TEXT DEFAULT 'Hi! How can I help?',
    ADD COLUMN IF NOT EXISTS position          TEXT DEFAULT 'bottom-right',
    ADD COLUMN IF NOT EXISTS theme             TEXT DEFAULT 'auto',  -- light|dark|auto
    ADD COLUMN IF NOT EXISTS faq_mode          TEXT DEFAULT 'auto';  -- auto|manual|off

-- One auto-provisioned embed per (project, agent). Manual embeds can coexist.
CREATE UNIQUE INDEX IF NOT EXISTS uq_embeds_auto_agent
    ON public.dash_agent_embeds(project_slug, agent_id)
    WHERE auto_provisioned = TRUE AND agent_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_embeds_agent
    ON public.dash_agent_embeds(project_slug, agent_id)
    WHERE agent_id IS NOT NULL;

COMMENT ON COLUMN public.dash_agent_embeds.agent_id IS
    'When set + auto_provisioned=true, this is the default per-agent embed surfaced on settings UI';
COMMENT ON COLUMN public.dash_agent_embeds.status IS
    'live = active + has origins; draft = no origins yet; disabled = soft-off';
