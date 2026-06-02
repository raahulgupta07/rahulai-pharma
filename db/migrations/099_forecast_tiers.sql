-- Forecast tiers: benchmark/audit table for the 3-tier forecasting router.
-- dash_ml_jobs already carries a `config` JSONB (see ml_worker/main.py), so the
-- timesfm 'forecast_zero' job type needs no schema change.
-- This table records every forecast run for benchmarking + audit.

CREATE TABLE IF NOT EXISTS dash.dash_forecast_runs (
    id            BIGSERIAL PRIMARY KEY,
    project_slug  TEXT NOT NULL,
    table_name    TEXT,
    tier          TEXT NOT NULL,            -- stats | mlforecast | timesfm
    horizon       INTEGER,                  -- periods forecast
    mase          DOUBLE PRECISION,
    mape          DOUBLE PRECISION,
    rmse          DOUBLE PRECISION,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_dash_forecast_runs_slug_created
    ON dash.dash_forecast_runs(project_slug, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_dash_forecast_runs_tier
    ON dash.dash_forecast_runs(tier);
