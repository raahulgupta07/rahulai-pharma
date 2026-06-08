"""
Recommendations
===============
Next Best Offer / collaborative filtering / market basket tools.

3 tools:
- next_best_offer(): per-customer top-N recommendations via item-based CF
- item_affinity(): market basket lift/confidence/support for one SKU
- popular_products(): trailing-window top sellers by revenue and units

All tools are read-only, parameterized, and capped at 50K transactions for
performance. Sparse matrices via scipy keep memory bounded; cosine similarity
via sklearn.
"""

import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


# Auto-detection candidates — order matters (first hit wins)
_CUSTOMER_CANDIDATES = ["customer_id", "user_id", "account_id"]
_SKU_CANDIDATES = ["sku_id", "product_id", "item_id", "upc"]
_BASKET_CANDIDATES = ["basket_id", "transaction_id", "order_id", "txn_id"]
_AMOUNT_CANDIDATES = ["amount", "total", "revenue", "net_sales"]
_DATE_CANDIDATES = ["date", "transaction_date", "order_date", "created_at", "timestamp", "purchase_date"]

_MAX_ROWS = 50_000


def _resolve_schema(project_slug: str) -> str:
    """Resolve project schema name with fallback."""
    try:
        from dash.templates.reconcile import _project_schema_name
        return _project_schema_name(project_slug)
    except Exception:
        import re
        return re.sub(r"[^a-z0-9_]", "_", project_slug.lower())[:63]


def _get_engine(engine=None):
    """Get SQL engine with fallback."""
    if engine is not None:
        return engine
    try:
        from db.session import get_sql_engine
        return get_sql_engine()
    except Exception:
        from db import get_sql_engine
        return get_sql_engine()


def _detect_column(available_cols, requested, candidates):
    """Pick a column from available_cols. Try requested first, then candidates."""
    cols_lower = {c.lower(): c for c in available_cols}
    if requested and requested.lower() in cols_lower:
        return cols_lower[requested.lower()]
    for cand in candidates:
        if cand.lower() in cols_lower:
            return cols_lower[cand.lower()]
    return None


def _load_table_columns(engine, schema: str, table: str):
    """Return list of column names for a table."""
    from sqlalchemy import inspect as sa_inspect
    insp = sa_inspect(engine)
    return [c["name"] for c in insp.get_columns(table, schema=schema)]


def _read_capped(engine, schema: str, table: str, cols, where_sql: str = "", limit: int = _MAX_ROWS):
    """Read up to `limit` rows from table for given columns."""
    import pandas as pd
    qualified = f'"{schema}"."{table}"'
    col_sql = ", ".join(f'"{c}"' for c in cols)
    sql = f"SELECT {col_sql} FROM {qualified}"
    if where_sql:
        sql += f" WHERE {where_sql}"
    sql += f" LIMIT {int(limit)}"
    return pd.read_sql(sql, engine)


def next_best_offer(
    project_slug: str,
    customer_id,
    table: str = "transactions",
    customer_col: str = "customer_id",
    sku_col: str = "sku_id",
    top_n: int = 5,
    engine=None,
):
    """For one customer, return top-N recommended SKUs via item-based collaborative filtering.

    CRITICAL: Call this tool EXACTLY ONCE per question. After receiving
    the result, STOP and synthesize the answer. Do NOT call this tool
    again with different parameters — the result is complete.

    Returns dict: {ok, customer_id, recommendations: [{sku, score, reason}], method}
    """
    # ML-based collaborative filtering (scipy + scikit-learn) has been removed
    # from this build to shrink the image. Graceful early return.
    return {
        "ok": False,
        "error": "Recommendations (ML) disabled in this build.",
        "recommendations": [],
    }


def item_affinity(
    project_slug: str,
    sku_id,
    table: str = "transactions",
    sku_col: str = "sku_id",
    basket_col: str = "basket_id",
    top_n: int = 10,
    engine=None,
):
    """Market basket analysis: top-N items frequently bought with target SKU.

    CRITICAL: Call this tool EXACTLY ONCE per question. After receiving
    the result, STOP and synthesize the answer. Do NOT call this tool
    again with different parameters — the result is complete.

    Returns dict: {ok, sku, affinities: [{sku, lift, confidence, support, co_count}]}
    """
    # Market basket analysis has been removed from this build to shrink the
    # image. Graceful early return.
    return {
        "ok": False,
        "error": "Recommendations (ML) disabled in this build.",
        "affinities": [],
    }


def popular_products(
    project_slug: str,
    table: str = "transactions",
    sku_col: str = "sku_id",
    amount_col: str = "amount",
    period_days: int = 30,
    top_n: int = 10,
    engine=None,
):
    """Top products by revenue and by units in trailing window.

    CRITICAL: Call this tool EXACTLY ONCE per question. After receiving
    the result, STOP and synthesize the answer. Do NOT call this tool
    again with different parameters — the result is complete.

    Returns dict: {ok, by_revenue, by_units, period_days}
    """
    try:
        import pandas as pd

        eng = _get_engine(engine)
        schema = _resolve_schema(project_slug)

        try:
            available = _load_table_columns(eng, schema, table)
        except Exception as e:
            return {"ok": False, "error": f"Could not read table '{schema}.{table}': {e}"}
        if not available:
            return {"ok": False, "error": f"Table '{schema}.{table}' has no columns"}

        sku = _detect_column(available, sku_col, _SKU_CANDIDATES)
        amount = _detect_column(available, amount_col, _AMOUNT_CANDIDATES)
        date_col = _detect_column(available, "", _DATE_CANDIDATES)

        if not sku:
            return {"ok": False, "error": f"No SKU column found (tried {sku_col}, {_SKU_CANDIDATES})"}

        cols = [sku]
        if amount:
            cols.append(amount)
        where_sql = ""
        if date_col and period_days and period_days > 0:
            cols.append(date_col)
            cutoff = (datetime.utcnow() - timedelta(days=int(period_days))).strftime("%Y-%m-%d")
            where_sql = f'"{date_col}" >= \'{cutoff}\''

        df = _read_capped(eng, schema, table, cols, where_sql=where_sql)
        if df.empty:
            return {
                "ok": True,
                "by_revenue": [],
                "by_units": [],
                "period_days": period_days,
                "note": "No rows in window" if where_sql else "No rows",
            }

        df = df.dropna(subset=[sku])
        df[sku] = df[sku].astype(str)

        # Units: row count per SKU
        units = df.groupby(sku).size().rename("units")

        # Revenue: sum of amount if available, else units as proxy
        if amount and amount in df.columns:
            df[amount] = pd.to_numeric(df[amount], errors="coerce").fillna(0)
            revenue = df.groupby(sku)[amount].sum().rename("revenue")
        else:
            revenue = units.rename("revenue").astype(float)

        combined = pd.concat([revenue, units], axis=1).fillna(0)

        by_rev = combined.sort_values("revenue", ascending=False).head(top_n)
        by_units = combined.sort_values("units", ascending=False).head(top_n)

        rev_list = [
            {"sku": idx, "revenue": round(float(r["revenue"]), 2), "units": int(r["units"])}
            for idx, r in by_rev.iterrows()
        ]
        unit_list = [
            {"sku": idx, "revenue": round(float(r["revenue"]), 2), "units": int(r["units"])}
            for idx, r in by_units.iterrows()
        ]

        return {
            "ok": True,
            "by_revenue": rev_list,
            "by_units": unit_list,
            "period_days": period_days,
        }
    except Exception as e:
        logger.exception("popular_products failed")
        return {"ok": False, "error": str(e)}
