-- Extraction plan audit table
CREATE TABLE IF NOT EXISTS public.dash_extraction_plans (
    id BIGSERIAL PRIMARY KEY,
    project_slug TEXT NOT NULL,
    table_name TEXT NOT NULL,
    source_file TEXT,
    sheet_name TEXT,
    file_hash TEXT,
    strategy TEXT NOT NULL,
    header_row INTEGER,
    skip_rows JSONB DEFAULT '[]'::jsonb,
    blocks JSONB DEFAULT '[]'::jsonb,
    row_count_in BIGINT,
    row_count_out BIGINT,
    llm_rescued BOOLEAN DEFAULT FALSE,
    rescue_reasoning TEXT,
    user_overrides JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_extraction_plans_project
    ON public.dash_extraction_plans (project_slug, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_extraction_plans_table
    ON public.dash_extraction_plans (project_slug, table_name);
CREATE INDEX IF NOT EXISTS idx_extraction_plans_hash
    ON public.dash_extraction_plans (file_hash) WHERE file_hash IS NOT NULL;
