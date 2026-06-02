"""
Forecast Tier Router (1-tier — stats only)
==========================================
Heavy mlforecast/LightGBM tier dropped; all forecasts route to statsforecast
AutoARIMA/AutoETS in-process. The `predict` tool's API is unchanged.

detect_exog_columns() retained for backward compat (callers may still
inspect for exog columns, but router no longer branches on it).
"""

import re
import logging

logger = logging.getLogger(__name__)

VALID_TIERS = {"stats"}

# Column name aliases that indicate exogenous (promo/price) drivers.
# Kept for backward compat — callers may still surface this info even
# though the router no longer branches on it.
_EXOG_ALIASES = (
    "promo", "promotion", "is_promo", "on_sale", "sale", "discount",
    "price", "unit_price", "list_price", "markdown", "holiday", "is_holiday",
    "campaign", "coupon", "deal", "offer", "advertised", "feature_flag",
)


def detect_exog_columns(df) -> list:
    """Find promo/price/discount/holiday style columns by name aliases.

    Matching is token-based (snake/camel/space split) so "sales" or "wholesale"
    do not match the "sale" alias, but "on_sale" / "sale_flag" do.

    Args:
        df: pandas DataFrame (or anything with a `.columns` iterable).

    Returns:
        list[str] of column names that look like exogenous drivers.
    """
    try:
        cols = list(df.columns)
    except Exception:
        return []

    found = []
    for col in cols:
        spaced = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", str(col))
        tokens = set(re.split(r"[^a-z0-9]+", spaced.lower()))
        tokens.discard("")
        for alias in _EXOG_ALIASES:
            parts = alias.split("_")
            if all(p in tokens for p in parts):
                found.append(col)
                break
    return found


def choose_tier(
    history_len: int = 0,
    series_count: int = 0,
    has_exog: bool = False,
    model_arg: str = "auto",
) -> str:
    """Choose a forecasting tier.

    Always returns 'stats' — the heavy mlforecast tier was dropped.
    Signature preserved so callers don't break.
    """
    if model_arg and model_arg != "auto":
        m = str(model_arg).strip().lower()
        if m not in VALID_TIERS:
            logger.warning(
                f"Unknown/unsupported model_arg '{model_arg}', "
                f"falling back to 'stats' (mlforecast tier dropped)"
            )
    return "stats"
