"""
CLV + Churn Risk Scoring
========================
Two customer-analytics tools for the Data Scientist agent.

- clv_score(): per-customer Customer Lifetime Value prediction.
  Uses `lifetimes` (BG/NBD + Gamma-Gamma) when available, falls
  back to a simple proxy (activity_rate × horizon × avg_order).

- churn_risk_score(): per-customer churn risk classification.
  Computes days_since_last_order vs median inter-order gap and
  bins into active / cooling / at_risk / churned.

Both tools auto-detect customer / date / amount columns when the
defaults are missing, cap at 200K rows, and parameterize all SQL.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


# ─────────────────────────── Helpers ───────────────────────────


_CUSTOMER_CANDIDATES = ["customer_id", "user_id", "account_id", "cust_id", "client_id"]
_DATE_CANDIDATES = ["ts", "order_date", "created_at", "txn_time", "sale_date", "date", "timestamp"]
_AMOUNT_CANDIDATES = ["amount", "total", "revenue", "net_sales", "sale_amount", "order_total", "price"]


def _resolve_column(df_cols, requested: str, candidates: list[str]) -> str | None:
    """Return requested col if present, else first matching candidate (case-insensitive)."""
    cols_lower = {c.lower(): c for c in df_cols}
    if requested and requested.lower() in cols_lower:
        return cols_lower[requested.lower()]
    for cand in candidates:
        if cand.lower() in cols_lower:
            return cols_lower[cand.lower()]
    return None


def _load_transactions(project_slug: str, table: str, max_rows: int = 200_000):
    """Load transaction table from project schema, capped at max_rows."""
    import pandas as pd
    from sqlalchemy import text
    from db.session import get_sql_engine
    from dash.templates.reconcile import _project_schema_name

    eng = get_sql_engine()
    if eng is None:
        raise RuntimeError("No database engine available")

    schema = _project_schema_name(project_slug)
    qualified = f'"{schema}"."{table}"'

    # Validate table exists by checking information_schema (parameterized)
    with eng.begin() as cn:
        exists = cn.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = :s AND table_name = :t LIMIT 1"
            ),
            {"s": schema, "t": table},
        ).fetchone()
    if not exists:
        raise ValueError(f"Table '{table}' not found in schema '{schema}'")

    # Read with cap. Table/schema names are validated above; safe to interpolate.
    df = pd.read_sql(f"SELECT * FROM {qualified} LIMIT {int(max_rows)}", eng)
    return df, schema


# ─────────────────────────── Tool 1: CLV ───────────────────────────


def clv_score(
    project_slug: str,
    table: str = "transactions",
    customer_col: str = "customer_id",
    date_col: str = "ts",
    amount_col: str = "amount",
    horizon_days: int = 365,
) -> dict[str, Any]:
    """Compute predicted Customer Lifetime Value per customer.

    CRITICAL: Call this tool EXACTLY ONCE per question. After receiving
    the result, STOP and synthesize the answer. Do NOT call this tool
    again with different parameters — the result is complete.

    Tries BG/NBD + Gamma-Gamma via the `lifetimes` library; falls back to a
    simple proxy: predicted_clv = (frequency / tenure_days) * horizon_days * avg_order.

    Returns dict with top_clv_customers, summary stats, and method tag.
    """
    try:
        import pandas as pd
        import numpy as np

        df, schema = _load_transactions(project_slug, table)
        if df.empty:
            return {"ok": False, "error": f"Table '{table}' is empty"}

        # Resolve columns
        cust = _resolve_column(df.columns, customer_col, _CUSTOMER_CANDIDATES)
        dt = _resolve_column(df.columns, date_col, _DATE_CANDIDATES)
        amt = _resolve_column(df.columns, amount_col, _AMOUNT_CANDIDATES)

        missing = []
        if not cust:
            missing.append(f"customer_col (tried {customer_col!r} + {_CUSTOMER_CANDIDATES})")
        if not dt:
            missing.append(f"date_col (tried {date_col!r} + {_DATE_CANDIDATES})")
        if not amt:
            missing.append(f"amount_col (tried {amount_col!r} + {_AMOUNT_CANDIDATES})")
        if missing:
            return {"ok": False, "error": "Could not resolve columns: " + "; ".join(missing),
                    "available_columns": list(df.columns)}

        # Coerce types
        df[dt] = pd.to_datetime(df[dt], errors="coerce")
        df[amt] = pd.to_numeric(df[amt], errors="coerce")
        df = df.dropna(subset=[cust, dt, amt])
        df = df[df[amt] > 0]
        if len(df) < 5:
            return {"ok": False, "error": f"Not enough valid rows after cleaning (got {len(df)})"}

        rows_analyzed = len(df)
        now = df[dt].max()  # use max observed date as "now" (avoids future-dated NOW issues)

        # Per-customer aggregates
        agg = df.groupby(cust).agg(
            first_date=(dt, "min"),
            last_date=(dt, "max"),
            frequency=(dt, "count"),
            total_revenue=(amt, "sum"),
            avg_order_value=(amt, "mean"),
        ).reset_index()

        agg["tenure_days"] = (now - agg["first_date"]).dt.days.clip(lower=1)
        agg["recency_days"] = (now - agg["last_date"]).dt.days.clip(lower=0)
        agg["activity_rate"] = agg["frequency"] / agg["tenure_days"]

        method = "simple_proxy"
        # Try lifetimes lib for proper BG/NBD + Gamma-Gamma
        try:
            from lifetimes import BetaGeoFitter, GammaGammaFitter
            from lifetimes.utils import summary_data_from_transaction_data

            summary = summary_data_from_transaction_data(
                df, customer_id_col=cust, datetime_col=dt, monetary_value_col=amt,
                observation_period_end=now,
            )
            # BG/NBD needs frequency > 0
            summary_pos = summary[summary["frequency"] > 0].copy()
            if len(summary_pos) >= 10:
                bgf = BetaGeoFitter(penalizer_coef=0.001)
                bgf.fit(summary_pos["frequency"], summary_pos["recency"], summary_pos["T"])

                ggf = GammaGammaFitter(penalizer_coef=0.001)
                # Gamma-Gamma requires non-zero monetary
                mon_pos = summary_pos[summary_pos["monetary_value"] > 0]
                if len(mon_pos) >= 10:
                    ggf.fit(mon_pos["frequency"], mon_pos["monetary_value"])

                    horizon_months = max(1, horizon_days / 30.0)
                    clv_series = ggf.customer_lifetime_value(
                        bgf,
                        summary_pos["frequency"],
                        summary_pos["recency"],
                        summary_pos["T"],
                        summary_pos["monetary_value"],
                        time=horizon_months,
                        freq="D",
                        discount_rate=0.0,
                    )
                    # Merge predicted_clv into agg
                    clv_df = clv_series.reset_index()
                    clv_df.columns = [cust, "predicted_clv"]
                    agg = agg.merge(clv_df, on=cust, how="left")
                    agg["predicted_clv"] = agg["predicted_clv"].fillna(0.0)
                    method = "lifetimes"
        except ImportError:
            logger.debug("lifetimes library not installed; using simple_proxy CLV")
        except Exception as e:
            logger.warning(f"lifetimes fit failed, falling back to simple_proxy: {e}")

        if method == "simple_proxy":
            agg["predicted_orders"] = agg["activity_rate"] * float(horizon_days)
            agg["predicted_clv"] = agg["predicted_orders"] * agg["avg_order_value"]

        # Confidence band
        def _confidence(freq, recency_d):
            if freq >= 10 and recency_d <= 60:
                return "high"
            if freq >= 5 and recency_d <= 120:
                return "medium"
            return "low"

        agg["confidence"] = [
            _confidence(int(f), int(r)) for f, r in zip(agg["frequency"], agg["recency_days"])
        ]

        # Top 50 by predicted_clv
        top = agg.sort_values("predicted_clv", ascending=False).head(50)
        top_list = []
        for _, r in top.iterrows():
            top_list.append({
                "customer_id": str(r[cust]),
                "predicted_clv": round(float(r["predicted_clv"]), 2),
                "tenure_days": int(r["tenure_days"]),
                "frequency": int(r["frequency"]),
                "avg_order": round(float(r["avg_order_value"]), 2),
                "activity_rate": round(float(r["activity_rate"]), 4),
                "total_revenue": round(float(r["total_revenue"]), 2),
                "confidence": r["confidence"],
            })

        clv_vals = agg["predicted_clv"].fillna(0.0)
        summary_stats = {
            "total_predicted_clv": round(float(clv_vals.sum()), 2),
            "avg_clv": round(float(clv_vals.mean()), 2),
            "median_clv": round(float(clv_vals.median()), 2),
            "customers": int(len(agg)),
            "horizon_days": int(horizon_days),
        }

        return {
            "ok": True,
            "rows_analyzed": int(rows_analyzed),
            "top_clv_customers": top_list,
            "summary": summary_stats,
            "method": method,
            "columns_used": {"customer": cust, "date": dt, "amount": amt},
        }
    except ValueError as e:
        # Expected condition (e.g. missing table in duplicated/empty schema) — don't spam tracebacks
        logger.info("clv_score skipped: %s", e)
        return {"ok": False, "error": str(e)}
    except Exception as e:
        logger.exception("clv_score failed")
        return {"ok": False, "error": str(e)}


# ─────────────────────────── Tool 2: Churn Risk ───────────────────────────


def churn_risk_score(
    project_slug: str,
    table: str = "transactions",
    customer_col: str = "customer_id",
    date_col: str = "ts",
    dormant_days: int = 60,
) -> dict[str, Any]:
    """Score every customer's churn risk based on inactivity vs typical cadence.

    CRITICAL: Call this tool EXACTLY ONCE per question. After receiving
    the result, STOP and synthesize the answer. Do NOT call this tool
    again with different parameters — the result is complete.

    score = days_since_last_order / max(median_inter_order_gap * 2, dormant_days)
      <0.5 active · 0.5–1.0 cooling · 1.0–2.0 at_risk · >2.0 churned

    Returns dict with distribution counts and top high-risk customers by LTV.
    """
    try:
        import pandas as pd
        import numpy as np

        df, schema = _load_transactions(project_slug, table)
        if df.empty:
            return {"ok": False, "error": f"Table '{table}' is empty"}

        cust = _resolve_column(df.columns, customer_col, _CUSTOMER_CANDIDATES)
        dt = _resolve_column(df.columns, date_col, _DATE_CANDIDATES)
        amt = _resolve_column(df.columns, "", _AMOUNT_CANDIDATES)  # amount optional for LTV ranking

        missing = []
        if not cust:
            missing.append(f"customer_col (tried {customer_col!r} + {_CUSTOMER_CANDIDATES})")
        if not dt:
            missing.append(f"date_col (tried {date_col!r} + {_DATE_CANDIDATES})")
        if missing:
            return {"ok": False, "error": "Could not resolve columns: " + "; ".join(missing),
                    "available_columns": list(df.columns)}

        df[dt] = pd.to_datetime(df[dt], errors="coerce")
        if amt:
            df[amt] = pd.to_numeric(df[amt], errors="coerce")
        df = df.dropna(subset=[cust, dt])
        if len(df) < 5:
            return {"ok": False, "error": f"Not enough valid rows after cleaning (got {len(df)})"}

        rows_analyzed = len(df)
        now = df[dt].max()

        # Compute global median inter-order gap (across customers with >=2 orders)
        df_sorted = df.sort_values([cust, dt])
        df_sorted["_prev_dt"] = df_sorted.groupby(cust)[dt].shift(1)
        gaps = (df_sorted[dt] - df_sorted["_prev_dt"]).dt.days.dropna()
        gaps = gaps[gaps > 0]
        median_gap = float(gaps.median()) if len(gaps) > 0 else float(dormant_days)

        # Per-customer aggregates
        agg_dict = {
            "frequency": (dt, "count"),
            "last_date": (dt, "max"),
            "first_date": (dt, "min"),
        }
        if amt:
            agg_dict["lifetime_value"] = (amt, "sum")
        agg = df.groupby(cust).agg(**agg_dict).reset_index()
        if not amt:
            agg["lifetime_value"] = 0.0

        agg["days_since"] = (now - agg["last_date"]).dt.days.clip(lower=0)
        threshold = max(median_gap * 2.0, float(dormant_days))
        agg["score"] = agg["days_since"] / threshold

        def _categorize(s: float) -> str:
            if s < 0.5:
                return "active"
            if s < 1.0:
                return "cooling"
            if s < 2.0:
                return "at_risk"
            return "churned"

        agg["category"] = agg["score"].apply(_categorize)

        distribution = {
            "active": int((agg["category"] == "active").sum()),
            "cooling": int((agg["category"] == "cooling").sum()),
            "at_risk": int((agg["category"] == "at_risk").sum()),
            "churned": int((agg["category"] == "churned").sum()),
        }

        # Top 100 high-risk by LTV (at_risk + churned)
        high_risk = agg[agg["category"].isin(["at_risk", "churned"])].copy()
        high_risk = high_risk.sort_values("lifetime_value", ascending=False).head(100)
        high_risk_list = []
        for _, r in high_risk.iterrows():
            high_risk_list.append({
                "customer_id": str(r[cust]),
                "days_since": int(r["days_since"]),
                "category": r["category"],
                "score": round(float(r["score"]), 2),
                "frequency": int(r["frequency"]),
                "lifetime_value": round(float(r["lifetime_value"]), 2),
            })

        total = len(agg)
        churn_rate = (distribution["at_risk"] + distribution["churned"]) / total if total else 0.0
        summary_text = (
            f"{total} customers analyzed · median inter-order gap {median_gap:.1f}d · "
            f"{distribution['active']} active · {distribution['cooling']} cooling · "
            f"{distribution['at_risk']} at_risk · {distribution['churned']} churned · "
            f"combined churn risk {churn_rate*100:.1f}%"
        )

        return {
            "ok": True,
            "rows_analyzed": int(rows_analyzed),
            "distribution": distribution,
            "high_risk_customers": high_risk_list,
            "median_inter_order_gap": round(median_gap, 2),
            "dormant_days_used": int(dormant_days),
            "summary": summary_text,
            "columns_used": {"customer": cust, "date": dt, "amount": amt or None},
        }
    except ValueError as e:
        # Expected condition (e.g. missing table in duplicated/empty schema) — don't spam tracebacks
        logger.info("churn_risk_score skipped: %s", e)
        return {"ok": False, "error": str(e)}
    except Exception as e:
        logger.exception("churn_risk_score failed")
        return {"ok": False, "error": str(e)}
