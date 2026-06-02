-- Migration 124: Per-table usage telemetry materialized view.
-- Aggregates dash_traces (chat/sql spans) into per-table stats so retrieval
-- can boost popular tables. SQL is parsed at refresh time via sqlglot
-- (Python-side) — this migration only builds the empty MV + function shells
-- so a Python-driven refresh can populate it. We use a regular MV
-- backed by a temp staging table populated from app code, because Postgres
-- cannot parse SQL with sqlglot. The Python refresh writes into a
-- table-shaped staging then we just have the MV select from a base view.
--
-- Strategy: a *base view* `v_table_usage_raw` extracts table refs via a
-- naive regexp (good enough for trivial INFORMATION) — Python refresh
-- handler then writes the *real* parsed stats into a real table
-- `dash_table_usage_stats`, and the MV `mv_table_usage` is just a snapshot.
--
-- Idempotent: IF NOT EXISTS / CREATE OR REPLACE throughout.

-- ── Real backing table populated by Python refresh ────────────────────────
CREATE TABLE IF NOT EXISTS public.dash_table_usage_stats (
    table_fqn        TEXT        PRIMARY KEY,
    query_count_7d   INTEGER     NOT NULL DEFAULT 0,
    query_count_30d  INTEGER     NOT NULL DEFAULT 0,
    last_used_at     TIMESTAMPTZ,
    distinct_users   INTEGER     NOT NULL DEFAULT 0,
    avg_latency_ms   NUMERIC,
    error_rate       NUMERIC,
    refreshed_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_dash_table_usage_stats_q30d
    ON public.dash_table_usage_stats (query_count_30d DESC);

CREATE INDEX IF NOT EXISTS idx_dash_table_usage_stats_last_used
    ON public.dash_table_usage_stats (last_used_at DESC NULLS LAST);

-- ── Materialized view (snapshot of the stats table) ───────────────────────
-- Drop+recreate-style is fine; the heavy lifting is in the stats table.
DROP MATERIALIZED VIEW IF EXISTS public.mv_table_usage;

CREATE MATERIALIZED VIEW public.mv_table_usage AS
SELECT
    table_fqn,
    query_count_7d,
    query_count_30d,
    last_used_at,
    distinct_users,
    avg_latency_ms,
    error_rate,
    refreshed_at
FROM public.dash_table_usage_stats
WITH NO DATA;

CREATE UNIQUE INDEX IF NOT EXISTS uq_mv_table_usage_fqn
    ON public.mv_table_usage (table_fqn);

CREATE INDEX IF NOT EXISTS idx_mv_table_usage_q30d
    ON public.mv_table_usage (query_count_30d DESC);

-- ── Refresh function — concurrent if possible, else plain ─────────────────
CREATE OR REPLACE FUNCTION public.refresh_mv_table_usage()
RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
    -- CONCURRENTLY needs the unique index above. Falls back to plain refresh.
    BEGIN
        REFRESH MATERIALIZED VIEW CONCURRENTLY public.mv_table_usage;
    EXCEPTION WHEN OTHERS THEN
        REFRESH MATERIALIZED VIEW public.mv_table_usage;
    END;
END;
$$;
