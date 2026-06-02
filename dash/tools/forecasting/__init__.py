"""
Forecasting package — 1-tier forecast routing (stats only).

Tier:
- stats : statsforecast AutoARIMA/AutoETS (classic, per-series fit)
"""

from .router import choose_tier, detect_exog_columns

__all__ = ["choose_tier", "detect_exog_columns"]
