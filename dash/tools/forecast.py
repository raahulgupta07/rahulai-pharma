"""
Forecast Tool
=============

Time series forecasting using Prophet. Auto-detects date columns,
fits model, returns forecast with confidence intervals.
"""

import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def run_forecast(
    table: str,
    date_column: str,
    value_column: str,
    periods: int = 3,
    frequency: str = "auto",
    _engine=None,
    _schema: str = "public",
) -> str:
    """Run time series forecast using Prophet.

    Args:
        table: Table name to forecast from.
        date_column: Column containing dates/timestamps.
        value_column: Column containing numeric values to forecast.
        periods: Number of future periods to predict (default 3).
        frequency: Frequency — 'D' daily, 'W' weekly, 'M' monthly, 'auto' to detect.
        _engine: SQLAlchemy engine (injected by tool builder).
        _schema: Schema name (injected by tool builder).

    Returns:
        JSON string with forecast results, trend, seasonality, and model quality.
    """
    if not _engine:
        return json.dumps({"error": "No database connection available"})

    try:
        import pandas as pd
        from sqlalchemy import text

        # 1. Fetch historical data
        query = f'SELECT "{date_column}", "{value_column}" FROM "{_schema}"."{table}" ORDER BY "{date_column}"'
        with _engine.connect() as conn:
            result = conn.execute(text(query))
            rows = result.fetchall()

        if len(rows) < 10:
            return json.dumps({
                "error": f"Not enough data for forecasting. Found {len(rows)} rows, need at least 10.",
                "suggestion": "Add more historical data or use LLM-based trend extrapolation instead."
            })

        # 2. Build DataFrame
        df = pd.DataFrame(rows, columns=["ds", "y"])

        # Parse dates
        df["ds"] = pd.to_datetime(df["ds"], format="mixed", errors="coerce")
        df = df.dropna(subset=["ds"])

        # Parse values — handle text like "$4.2M", "1,234", etc.
        def parse_value(v):
            if isinstance(v, (int, float)):
                return float(v)
            s = str(v).replace(",", "").replace("$", "").strip()
            multiplier = 1
            if s.upper().endswith("K"):
                multiplier = 1_000
                s = s[:-1]
            elif s.upper().endswith("M"):
                multiplier = 1_000_000
                s = s[:-1]
            elif s.upper().endswith("B"):
                multiplier = 1_000_000_000
                s = s[:-1]
            elif s.upper().endswith("T"):
                multiplier = 1_000_000_000_000
                s = s[:-1]
            try:
                return float(s) * multiplier
            except (ValueError, TypeError):
                return None

        df["y"] = df["y"].apply(parse_value)
        df = df.dropna(subset=["y"])

        if len(df) < 10:
            return json.dumps({
                "error": f"Only {len(df)} valid numeric rows after parsing. Need at least 10.",
            })

        # 3. Auto-detect frequency
        if frequency == "auto":
            if len(df) >= 2:
                avg_gap = (df["ds"].max() - df["ds"].min()).days / len(df)
                if avg_gap <= 2:
                    frequency = "D"
                elif avg_gap <= 10:
                    frequency = "W"
                else:
                    frequency = "MS"
            else:
                frequency = "MS"

        # 4. Fit Prophet model
        from prophet import Prophet

        model = Prophet(
            yearly_seasonality="auto",
            weekly_seasonality=frequency == "D",
            daily_seasonality=False,
            changepoint_prior_scale=0.05,
        )
        # Suppress Prophet logs
        import io
        import sys
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            model.fit(df)
        finally:
            sys.stdout = old_stdout

        # 5. Make forecast
        future = model.make_future_dataframe(periods=periods, freq=frequency)
        forecast = model.predict(future)

        # 6. Extract results
        historical = forecast[forecast["ds"] <= df["ds"].max()]
        predictions = forecast[forecast["ds"] > df["ds"].max()]

        forecast_data = []
        for _, row in predictions.iterrows():
            forecast_data.append({
                "date": row["ds"].strftime("%Y-%m-%d"),
                "value": round(row["yhat"], 2),
                "lower": round(row["yhat_lower"], 2),
                "upper": round(row["yhat_upper"], 2),
            })

        # 7. Calculate model quality (MAPE on last 20% of data)
        split = int(len(df) * 0.8)
        if split > 5:
            train_df = df.iloc[:split]
            test_df = df.iloc[split:]
            test_model = Prophet(
                yearly_seasonality="auto",
                weekly_seasonality=frequency == "D",
                daily_seasonality=False,
            )
            sys.stdout = io.StringIO()
            try:
                test_model.fit(train_df)
            finally:
                sys.stdout = old_stdout
            test_future = test_model.make_future_dataframe(periods=len(test_df), freq=frequency)
            test_forecast = test_model.predict(test_future)
            test_pred = test_forecast.iloc[split:]["yhat"].values[:len(test_df)]
            test_actual = test_df["y"].values
            if len(test_pred) == len(test_actual) and len(test_actual) > 0:
                mape = (abs(test_actual - test_pred) / (abs(test_actual) + 1e-10)).mean() * 100
                model_quality = f"MAPE: {mape:.1f}%"
                if mape < 10:
                    quality_label = "good"
                elif mape < 25:
                    quality_label = "moderate"
                else:
                    quality_label = "poor"
            else:
                mape = None
                quality_label = "unknown"
                model_quality = "insufficient test data"
        else:
            mape = None
            quality_label = "unknown"
            model_quality = "too few rows for validation"

        # 8. Trend direction
        if len(historical) >= 2:
            first_half = historical.iloc[:len(historical)//2]["yhat"].mean()
            second_half = historical.iloc[len(historical)//2:]["yhat"].mean()
            if second_half > first_half * 1.05:
                trend = "increasing"
            elif second_half < first_half * 0.95:
                trend = "decreasing"
            else:
                trend = "stable"
            growth_rate = ((second_half / first_half) - 1) * 100 if first_half > 0 else 0
        else:
            trend = "unknown"
            growth_rate = 0

        # 9. Seasonality
        seasonality = {}
        if hasattr(model, "seasonalities"):
            for name in model.seasonalities:
                seasonality[name] = True

        return json.dumps({
            "status": "ok",
            "table": table,
            "date_column": date_column,
            "value_column": value_column,
            "historical_rows": len(df),
            "forecast": forecast_data,
            "trend": trend,
            "growth_rate": f"{growth_rate:+.1f}%",
            "seasonality": seasonality if seasonality else {"none_detected": True},
            "model_quality": f"{quality_label} ({model_quality})",
            "frequency": frequency,
            "periods_forecasted": periods,
        })

    except ImportError:
        return json.dumps({"error": "Prophet not installed. Install with: pip install prophet"})
    except Exception as e:
        logger.error(f"Forecast error: {e}")
        return json.dumps({"error": f"Forecast failed: {str(e)[:200]}"})
