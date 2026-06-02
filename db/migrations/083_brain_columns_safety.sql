-- 083_brain_columns_safety.sql
-- Permanently ensure public.dash_company_brain has source_id column.
-- Recurring regression: app/brain.py queries source_id but cold installs
-- (and certain pre-077 setups) ship the table without it. Idempotent ALTER.

ALTER TABLE public.dash_company_brain
    ADD COLUMN IF NOT EXISTS source_id BIGINT;

CREATE INDEX IF NOT EXISTS idx_brain_source_id
    ON public.dash_company_brain(source_id)
    WHERE source_id IS NOT NULL;
