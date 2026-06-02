-- 149_action_registry.sql
-- Phase 2 — Internal Action Registry
--
-- Defines named, parameterized HTTP actions (POST/PUT/PATCH/DELETE) that the
-- Engineer agent can request via `request_action(name, payload_json, reason)`.
-- Execution is gated through `public.dash_hitl_requests` approval flow.
--
-- Templates support {{var}} placeholders rendered from the payload JSON at
-- request time. Idempotent. Schema-qualified to `public`.

CREATE TABLE IF NOT EXISTS public.dash_action_registry (
    id                BIGSERIAL PRIMARY KEY,
    project_id        BIGINT,                                  -- nullable: global actions allowed
    name              TEXT NOT NULL,                            -- e.g. 'send_slack_alert'
    description       TEXT,
    method            TEXT NOT NULL CHECK (method IN ('POST','PUT','PATCH','DELETE')),
    url_template      TEXT NOT NULL,                            -- jinja-style {{var}} placeholders allowed
    header_template   JSONB NOT NULL DEFAULT '{}'::jsonb,
    body_template     JSONB NOT NULL DEFAULT '{}'::jsonb,
    requires_approval BOOLEAN NOT NULL DEFAULT TRUE,
    min_approvals     INT NOT NULL DEFAULT 1,
    enabled           BOOLEAN NOT NULL DEFAULT TRUE,
    created_by        TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One (project, name) pair per row. NULL project_id allowed (treated as global).
CREATE UNIQUE INDEX IF NOT EXISTS uq_action_registry_project_name
    ON public.dash_action_registry (project_id, name);

CREATE INDEX IF NOT EXISTS idx_action_registry_project_enabled
    ON public.dash_action_registry (project_id, enabled);
