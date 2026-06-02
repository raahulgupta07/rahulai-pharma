"""
Customer Intelligence
=====================
Customer-focused analytical tools for the Analyst / Data Scientist agent.
Pure Python — pandas + SQL, no agents, no LLM.

2 tools:
- rfm_score(): Recency-Frequency-Monetary segmentation (11 standard labels)
- cohort_curve(): Cohort retention matrix (week / month / quarter)

Both tools:
- Auto-detect customer/date/amount columns when not provided
- Cap analysis volume for safety (RFM 100K rows; cohort 12x12 matrix)
- Use project-scoped SQL engine + schema (matches ml_models.py style)
- Return dict with {"ok": bool, ...} on success / failure
"""

import logging

logger = logging.getLogger(__name__)


# Heuristic column-name candidates (lowercased) for auto-detection
_CUSTOMER_HINTS = ("customer_id", "customer", "user_id", "user", "account_id",
                   "account", "client_id", "client", "buyer_id", "member_id")
_DATE_HINTS = ("order_date", "purchase_date", "transaction_date", "invoice_date",
               "created_at", "created_on", "date", "ts", "timestamp", "occurred_at")
_AMOUNT_HINTS = ("total", "amount", "revenue", "sales", "price", "value",
                 "grand_total", "subtotal", "net_amount", "gross_amount", "spend")


def _resolve_engine_schema(project_slug: str):
    """Get (engine, schema) for a project — matches ml_models.py pattern."""
    from db.session import get_sql_engine
    from dash.templates.reconcile import _project_schema_name
    eng = get_sql_engine()
    schema = _project_schema_name(project_slug)
    return eng, schema


def _autodetect_column(columns, hints):
    """Pick first matching column (case-insensitive) from a hint list."""
    lower_map = {c.lower(): c for c in columns}
    # Exact-match pass
    for h in hints:
        if h in lower_map:
            return lower_map[h]
    # Substring pass
    for h in hints:
        for lc, real in lower_map.items():
            if h in lc:
                return real
    return None


def _list_table_columns(engine, schema: str, table: str):
    """Return list of column names for a table in a project schema."""
    from sqlalchemy import inspect as sa_inspect
    insp = sa_inspect(engine)
    try:
        cols = insp.get_columns(table, schema=schema)
        return [c["name"] for c in cols]
    except Exception:
        return []


def _classify_rfm_segment(r: int, f: int, m: int) -> str:
    """Map an (R, F, M) quintile triple to one of 11 standard RFM labels."""
    # Champions
    if r == 5 and f == 5 and m == 5:
        return "Champions"
    # Cannot Lose Them (was great, gone quiet)
    if r == 1 and f >= 4:
        return "Cannot Lose"
    # At Risk (used to buy often, slipping)
    if r <= 2 and f >= 2:
        return "At Risk"
    # Lost (low everything)
    if r == 1 and f == 1:
        return "Lost"
    # Hibernating (low R, low F)
    if r <= 2 and f <= 2:
        return "Hibernating"
    # Loyal Customers
    if r >= 4 and f >= 4:
        return "Loyal Customers"
    # New Customers (recent, only 1 purchase)
    if r >= 4 and f == 1:
        return "New Customers"
    # Promising (somewhat recent, 1 purchase)
    if r in (3, 4) and f == 1:
        return "Promising"
    # Potential Loyalists (recent, a few purchases)
    if r >= 3 and 2 <= f <= 3:
        return "Potential Loyalists"
    # About to Sleep (mid-low recency, low frequency)
    if r in (2, 3) and f <= 2:
        return "About to Sleep"
    # Need Attention (middle of the road)
    if r == 3 and f == 3:
        return "Need Attention"
    # Fallback bucket
    return "Need Attention"


def rfm_score(project_slug: str,
              table: str = "",
              customer_col: str = "",
              date_col: str = "",
              amount_col: str = "") -> dict:
    """Compute Recency / Frequency / Monetary segmentation for a transactions table.

    CRITICAL: Call this tool EXACTLY ONCE per question. After receiving
    the result, STOP and synthesize the answer. Do NOT call this tool
    again with different parameters — the result is complete.

    Returns dict: {"ok": True, "rows_analyzed": N, "segments": {...},
                   "top_customers": [...], "summary": "...",
                   "table": ..., "columns": {...}}
    """
    try:
        import pandas as pd
        from sqlalchemy import text

        eng, schema = _resolve_engine_schema(project_slug)

        # Resolve table
        if not table:
            from sqlalchemy import inspect as sa_inspect
            insp = sa_inspect(eng)
            tables = insp.get_table_names(schema=schema) or []
            if not tables:
                return {"ok": False, "error": "no tables in project schema"}
            # Prefer a table that actually has customer + date + amount-like columns
            chosen = None
            for t in tables:
                cols = _list_table_columns(eng, schema, t)
                if (_autodetect_column(cols, _CUSTOMER_HINTS) and
                    _autodetect_column(cols, _DATE_HINTS) and
                    _autodetect_column(cols, _AMOUNT_HINTS)):
                    chosen = t
                    break
            table = chosen or tables[0]

        cols = _list_table_columns(eng, schema, table)
        if not cols:
            return {"ok": False, "error": f"table '{table}' has no readable columns"}

        customer_col = customer_col or _autodetect_column(cols, _CUSTOMER_HINTS) or ""
        date_col = date_col or _autodetect_column(cols, _DATE_HINTS) or ""
        amount_col = amount_col or _autodetect_column(cols, _AMOUNT_HINTS) or ""

        missing = [n for n, v in (("customer_col", customer_col),
                                  ("date_col", date_col),
                                  ("amount_col", amount_col)) if not v]
        if missing:
            return {"ok": False, "error": f"could not auto-detect columns: {missing}",
                    "available_columns": cols}

        for c in (customer_col, date_col, amount_col):
            if c not in cols:
                return {"ok": False, "error": f"column '{c}' not found in '{table}'",
                        "available_columns": cols}

        # Aggregate per customer (cap 100K customers for memory safety)
        sql = (
            f'SELECT "{customer_col}" AS customer, '
            f'MAX("{date_col}"::text) AS last_date, '
            f'COUNT(*) AS freq, '
            f'SUM(CASE WHEN "{amount_col}"::text ~ \'^-?[0-9.]+$\' '
            f'         THEN "{amount_col}"::numeric ELSE 0 END) AS total '
            f'FROM "{schema}"."{table}" '
            f'WHERE "{customer_col}" IS NOT NULL '
            f'GROUP BY "{customer_col}" '
            f'LIMIT 100000'
        )
        try:
            df = pd.read_sql(text(sql), eng)
        except Exception:
            # Fallback: amount may already be numeric, type cast can fail — try simpler query
            sql_simple = (
                f'SELECT "{customer_col}" AS customer, '
                f'MAX("{date_col}") AS last_date, '
                f'COUNT(*) AS freq, '
                f'SUM("{amount_col}") AS total '
                f'FROM "{schema}"."{table}" '
                f'WHERE "{customer_col}" IS NOT NULL '
                f'GROUP BY "{customer_col}" '
                f'LIMIT 100000'
            )
            df = pd.read_sql(text(sql_simple), eng)

        if df.empty:
            return {"ok": False, "error": "no data"}

        df["last_date"] = pd.to_datetime(df["last_date"], errors="coerce")
        df = df.dropna(subset=["last_date"])
        if df.empty:
            return {"ok": False, "error": "no parseable dates"}

        df["total"] = pd.to_numeric(df["total"], errors="coerce").fillna(0.0)
        df["freq"] = pd.to_numeric(df["freq"], errors="coerce").fillna(0).astype(int)

        # Reference date = the latest transaction date observed
        ref_date = df["last_date"].max()
        df["days_since"] = (ref_date - df["last_date"]).dt.days.clip(lower=0)

        # Quintile scoring (qcut, duplicates='drop' to handle ties)
        def _safe_qcut(s, ascending_better: bool):
            try:
                ranked = pd.qcut(s.rank(method="first"), 5, labels=[1, 2, 3, 4, 5])
                ranked = ranked.astype(int)
            except Exception:
                ranked = pd.Series([3] * len(s), index=s.index)
            if not ascending_better:
                # for recency (lower is better), invert
                ranked = 6 - ranked
            return ranked

        # R: lower days_since = better → invert
        df["R"] = _safe_qcut(df["days_since"], ascending_better=False)
        # F, M: higher = better
        df["F"] = _safe_qcut(df["freq"], ascending_better=True)
        df["M"] = _safe_qcut(df["total"], ascending_better=True)

        df["segment"] = df.apply(
            lambda row: _classify_rfm_segment(int(row["R"]), int(row["F"]), int(row["M"])),
            axis=1,
        )

        # Exact counts across ALL customers (not just top N)
        segments = df["segment"].value_counts().to_dict()

        # Exact per-segment revenue + rich rollup across ALL customers
        try:
            seg_group = df.groupby("segment", dropna=False)
            segment_revenue = {str(k): float(v) for k, v in seg_group["total"].sum().to_dict().items()}
            _total_n = int(len(df)) or 1
            segments_with_pct: dict[str, dict] = {}
            for seg_name, sub in seg_group:
                sub_n = int(len(sub))
                segments_with_pct[str(seg_name)] = {
                    "count": sub_n,
                    "pct": round(sub_n / _total_n * 100.0, 2),
                    "avg_recency": float(sub["days_since"].mean()) if sub_n else 0.0,
                    "avg_frequency": float(sub["freq"].mean()) if sub_n else 0.0,
                    "avg_monetary": float(sub["total"].mean()) if sub_n else 0.0,
                    "total_revenue": float(sub["total"].sum()) if sub_n else 0.0,
                }
        except Exception as _e:
            logger.debug("segment rollup failed: %s", _e)
            segment_revenue = {}
            segments_with_pct = {}

        # Top 10 by monetary value
        top = (df.sort_values("total", ascending=False)
                 .head(10)[["customer", "freq", "total", "days_since",
                            "R", "F", "M", "segment"]])
        top_customers = []
        for _, row in top.iterrows():
            top_customers.append({
                "customer": str(row["customer"]),
                "frequency": int(row["freq"]),
                "monetary": float(row["total"]),
                "recency_days": int(row["days_since"]),
                "rfm": f"{int(row['R'])}{int(row['F'])}{int(row['M'])}",
                "segment": row["segment"],
            })

        # Summary
        n = len(df)
        champions = segments.get("Champions", 0)
        at_risk = segments.get("At Risk", 0)
        lost = segments.get("Lost", 0)
        summary = (
            f"Analyzed {n:,} customers from '{table}'. "
            f"Champions: {champions} ({champions/n:.0%}); "
            f"At Risk: {at_risk} ({at_risk/n:.0%}); "
            f"Lost: {lost} ({lost/n:.0%}). "
            f"Reference date: {ref_date.date()}."
        )

        return {
            "ok": True,
            "rows_analyzed": int(n),
            "segments": {k: int(v) for k, v in segments.items()},
            "segment_revenue": segment_revenue,
            "segments_with_pct": segments_with_pct,
            "top_customers": top_customers,
            "summary": summary,
            "table": table,
            "columns": {"customer": customer_col, "date": date_col, "amount": amount_col},
            "reference_date": str(ref_date.date()),
        }
    except Exception as e:
        logger.exception("rfm_score failed")
        return {"ok": False, "error": str(e)}


def cohort_curve(project_slug: str,
                 table: str = "",
                 customer_col: str = "",
                 date_col: str = "",
                 period: str = "month") -> dict:
    """Build cohort retention matrix.

    CRITICAL: Call this tool EXACTLY ONCE per question. After receiving
    the result, STOP and synthesize the answer. Do NOT call this tool
    again with different parameters — the result is complete.

    period: 'week' | 'month' | 'quarter'

    Returns: {"ok": True, "cohorts": [...], "periods": [0..N],
              "matrix": [[100, 45, ...], ...], "cohort_sizes": [...],
              "summary": "...", "period": "month"}
    """
    try:
        import pandas as pd
        from sqlalchemy import text

        period = (period or "month").lower()
        if period not in ("week", "month", "quarter"):
            return {"ok": False, "error": f"period must be week|month|quarter, got '{period}'"}

        trunc_unit = period  # postgres DATE_TRUNC accepts 'week' / 'month' / 'quarter'

        eng, schema = _resolve_engine_schema(project_slug)

        # Resolve table
        if not table:
            from sqlalchemy import inspect as sa_inspect
            insp = sa_inspect(eng)
            tables = insp.get_table_names(schema=schema) or []
            if not tables:
                return {"ok": False, "error": "no tables in project schema"}
            chosen = None
            for t in tables:
                cols = _list_table_columns(eng, schema, t)
                if (_autodetect_column(cols, _CUSTOMER_HINTS) and
                    _autodetect_column(cols, _DATE_HINTS)):
                    chosen = t
                    break
            table = chosen or tables[0]

        cols = _list_table_columns(eng, schema, table)
        if not cols:
            return {"ok": False, "error": f"table '{table}' has no readable columns"}

        customer_col = customer_col or _autodetect_column(cols, _CUSTOMER_HINTS) or ""
        date_col = date_col or _autodetect_column(cols, _DATE_HINTS) or ""

        missing = [n for n, v in (("customer_col", customer_col),
                                  ("date_col", date_col)) if not v]
        if missing:
            return {"ok": False, "error": f"could not auto-detect columns: {missing}",
                    "available_columns": cols}

        for c in (customer_col, date_col):
            if c not in cols:
                return {"ok": False, "error": f"column '{c}' not found in '{table}'",
                        "available_columns": cols}

        # Pull (customer, period) pairs
        sql = (
            f'SELECT "{customer_col}" AS customer, '
            f'DATE_TRUNC(\'{trunc_unit}\', "{date_col}"::timestamp) AS period_start '
            f'FROM "{schema}"."{table}" '
            f'WHERE "{customer_col}" IS NOT NULL AND "{date_col}" IS NOT NULL '
            f'LIMIT 1000000'
        )
        df = pd.read_sql(text(sql), eng)
        if df.empty:
            return {"ok": False, "error": "no data"}

        df["period_start"] = pd.to_datetime(df["period_start"], errors="coerce")
        df = df.dropna(subset=["period_start"])
        if df.empty:
            return {"ok": False, "error": "no parseable dates"}

        # Cohort = customer's first period
        first = df.groupby("customer")["period_start"].min().rename("cohort")
        df = df.merge(first, on="customer", how="left")

        # Period index = number of (week/month/quarter) units between cohort and activity
        if period == "week":
            df["period_idx"] = ((df["period_start"] - df["cohort"]).dt.days // 7).astype(int)
            label_fmt = lambda d: d.strftime("%Y-W%U")
        elif period == "quarter":
            df["period_idx"] = (
                (df["period_start"].dt.year - df["cohort"].dt.year) * 4 +
                (df["period_start"].dt.quarter - df["cohort"].dt.quarter)
            ).astype(int)
            label_fmt = lambda d: f"{d.year}-Q{d.quarter}"
        else:  # month
            df["period_idx"] = (
                (df["period_start"].dt.year - df["cohort"].dt.year) * 12 +
                (df["period_start"].dt.month - df["cohort"].dt.month)
            ).astype(int)
            label_fmt = lambda d: d.strftime("%Y-%m")

        df = df[df["period_idx"] >= 0]
        if df.empty:
            return {"ok": False, "error": "no valid cohort/period rows"}

        # Cap to last 12 cohorts × 12 periods
        all_cohorts_sorted = sorted(df["cohort"].unique())
        last_cohorts = all_cohorts_sorted[-12:]
        df = df[df["cohort"].isin(last_cohorts)]
        df = df[df["period_idx"] < 12]

        # Build retention matrix: unique customers per (cohort, period_idx)
        active = df.groupby(["cohort", "period_idx"])["customer"].nunique().unstack(fill_value=0)
        active = active.reindex(columns=range(12), fill_value=0)
        active = active.sort_index()

        cohort_sizes = active[0].replace(0, pd.NA)
        retention = active.div(cohort_sizes, axis=0).fillna(0) * 100.0
        retention = retention.round(1)

        cohorts_labels = [label_fmt(pd.Timestamp(c)) for c in retention.index]
        matrix = retention.values.tolist()
        sizes = [int(s) if pd.notna(s) else 0 for s in cohort_sizes.tolist()]

        # Summary: average retention at period 1 (immediately after first)
        if len(retention.columns) > 1:
            avg_p1 = float(retention.iloc[:, 1].mean()) if len(retention) else 0.0
        else:
            avg_p1 = 0.0
        summary = (
            f"Cohort analysis on '{table}' by {period}. "
            f"{len(cohorts_labels)} cohorts, up to 12 {period} periods. "
            f"Avg retention at period 1: {avg_p1:.1f}%. "
            f"Largest cohort: {max(sizes) if sizes else 0} customers."
        )

        return {
            "ok": True,
            "cohorts": cohorts_labels,
            "periods": list(range(12)),
            "matrix": matrix,
            "cohort_sizes": sizes,
            "summary": summary,
            "period": period,
            "table": table,
            "columns": {"customer": customer_col, "date": date_col},
        }
    except Exception as e:
        logger.exception("cohort_curve failed")
        return {"ok": False, "error": str(e)}
