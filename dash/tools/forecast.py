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
        JSON string. Forecasting (ML) is disabled in this build.
    """
    # ML-based forecasting (Prophet) has been removed from this build to shrink
    # the image. This is a graceful stub that fails soft.
    return json.dumps({"error": "Forecasting is disabled in this build.", "ok": False})
