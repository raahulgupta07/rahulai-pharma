"""MRR / ARR / Retention / Cohort analytics engine.

Auto-detects subscription tables and columns inside each project schema and
computes month-over-month MRR breakdown, retention, and cohort survival.

All math is correctness-first (Markov-style customer-level tracking, not
aggregate deltas) — fine for v1 since we cap row scan at 500K.

Public API (re-exported by `app/mrr_analytics.py` + the Data Scientist agent):

    detect_subscription_schema(slug)
    compute_mrr(slug, as_of_date=None)
    compute_mrr_breakdown(slug, period_start, period_end)
    compute_retention(slug, period_start, period_end)
    cohort_survival(slug, cohort_window='month', max_periods=24)
    mrr_trend(slug, months=12)

All functions return dicts. On failure they return ``{"ok": False, "error": ...}``
rather than raising, so the API layer + agent tools fail soft.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

# ─────────────────────────── Detection candidates ───────────────────────────

_TABLE_CANDIDATES = [
    "subscriptions", "subscription", "plans", "billing_cycles",
    "recurring", "recurring_revenue", "saas_subscriptions",
    "customer_subscriptions", "memberships", "billing",
]

_MRR_COL_CANDIDATES = [
    "mrr", "monthly_amount", "monthly_price", "monthly_revenue",
    "recurring_revenue", "monthly_recurring_revenue", "amount", "price",
    "subscription_amount",
]

_CUSTOMER_COL_CANDIDATES = [
    "customer_id", "account_id", "user_id", "tenant_id", "workspace_id",
    "subscriber_id", "client_id", "org_id", "company_id", "cust_id",
]

_PLAN_COL_CANDIDATES = [
    "plan", "plan_id", "plan_name", "tier", "product_id", "package",
]

_STARTED_COL_CANDIDATES = [
    "started_at", "start_date", "subscription_start", "activated_at",
    "created_at", "signup_date", "begin_date",
]

_CANCELED_COL_CANDIDATES = [
    "canceled_at", "cancelled_at", "ended_at", "end_date",
    "cancellation_date", "terminated_at", "expired_at",
]

_STATUS_COL_CANDIDATES = [
    "status", "state", "subscription_status", "active",
]

_BILLING_CYCLE_CANDIDATES = [
    "billing_cycle", "interval", "frequency", "billing_period",
    "billing_interval", "cycle",
]


# ─────────────────────────── helpers ───────────────────────────


_MAX_ROWS = 500_000


def _project_schema(slug: str) -> str:
    return re.sub(r"[^a-z0-9_]", "_", slug.lower())[:63]


def _ro_engine(slug: str):
    from db.session import get_project_readonly_engine
    return get_project_readonly_engine(slug)


def _list_tables(slug: str) -> list[str]:
    from sqlalchemy import text
    schema = _project_schema(slug)
    eng = _ro_engine(slug)
    with eng.begin() as cn:
        rows = cn.execute(text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = :s AND table_type IN ('BASE TABLE','VIEW')"
        ), {"s": schema}).fetchall()
    return [r[0] for r in rows]


def _list_columns(slug: str, table: str) -> list[dict]:
    from sqlalchemy import text
    schema = _project_schema(slug)
    eng = _ro_engine(slug)
    with eng.begin() as cn:
        rows = cn.execute(text(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = :s AND table_name = :t"
        ), {"s": schema, "t": table}).fetchall()
    return [{"name": r[0], "data_type": r[1]} for r in rows]


def _find_column(cols: list[str], candidates: list[str]) -> str | None:
    """Case-insensitive exact-match. Returns actual column name or None."""
    lower = {c.lower(): c for c in cols}
    for cand in candidates:
        if cand.lower() in lower:
            return lower[cand.lower()]
    # contains-token fallback
    for cand in candidates:
        for c in cols:
            if cand.lower() in c.lower():
                return c
    return None


# ─────────────────────────── schema detection ───────────────────────────


def detect_subscription_schema(slug: str) -> dict[str, Any]:
    """Detect a subscription table + key columns in the project schema.

    Returns:
        {
          "found": bool,
          "table": str | None,
          "columns_map": {customer_id, mrr, plan, started_at, canceled_at, status, billing_cycle},
          "confidence": float,
          "suggestions": [str, ...],   # only when found=False
        }
    """
    try:
        tables = _list_tables(slug)
    except Exception as e:
        return {
            "found": False,
            "table": None,
            "columns_map": {},
            "confidence": 0.0,
            "error": f"schema introspection failed: {e}",
            "suggestions": [
                f"Create a 'subscriptions' table with columns "
                f"(customer_id, mrr, started_at, canceled_at, status).",
                "Apply the SaaS template from /api/agent-templates.",
            ],
        }

    if not tables:
        return {
            "found": False,
            "table": None,
            "columns_map": {},
            "confidence": 0.0,
            "suggestions": [
                "No tables in project schema. Upload subscription data first.",
            ],
        }

    table_lower = {t.lower(): t for t in tables}
    chosen: tuple[str, float] | None = None
    for cand in _TABLE_CANDIDATES:
        if cand.lower() in table_lower:
            chosen = (table_lower[cand.lower()], 1.0)
            break
    if chosen is None:
        # token fallback
        for t in tables:
            tl = t.lower()
            if "subscrib" in tl or "billing" in tl or "recurring" in tl:
                chosen = (t, 0.7)
                break

    if chosen is None:
        return {
            "found": False,
            "table": None,
            "columns_map": {},
            "confidence": 0.0,
            "suggestions": [
                "Subscription table not detected. Expected one of: "
                + ", ".join(_TABLE_CANDIDATES[:5]) + ".",
                "Apply the SaaS template (POST /api/projects/{slug}/apply-agent-template).",
                "Or rename your existing table to match.",
            ],
        }

    table, base_conf = chosen
    cols = [c["name"] for c in _list_columns(slug, table)]

    customer_col = _find_column(cols, _CUSTOMER_COL_CANDIDATES)
    mrr_col = _find_column(cols, _MRR_COL_CANDIDATES)
    plan_col = _find_column(cols, _PLAN_COL_CANDIDATES)
    started_col = _find_column(cols, _STARTED_COL_CANDIDATES)
    canceled_col = _find_column(cols, _CANCELED_COL_CANDIDATES)
    status_col = _find_column(cols, _STATUS_COL_CANDIDATES)
    billing_cycle_col = _find_column(cols, _BILLING_CYCLE_CANDIDATES)

    # MRR-correctness boost: if the value column is "amount" + we have a billing_cycle col,
    # we'll filter to monthly cycles.
    columns_map = {
        "customer_id": customer_col,
        "mrr": mrr_col,
        "plan": plan_col,
        "started_at": started_col,
        "canceled_at": canceled_col,
        "status": status_col,
        "billing_cycle": billing_cycle_col,
    }

    # Confidence — penalize when required columns missing
    needed = [customer_col, mrr_col, started_col]
    missing_required = sum(1 for c in needed if c is None)
    confidence = base_conf - 0.25 * missing_required
    confidence = max(0.0, min(1.0, confidence))

    if missing_required > 1:
        return {
            "found": False,
            "table": table,
            "columns_map": columns_map,
            "confidence": confidence,
            "suggestions": [
                f"Found '{table}' but missing required columns "
                f"(customer_id / mrr / started_at). Add or rename columns.",
            ],
        }

    return {
        "found": True,
        "table": table,
        "columns_map": columns_map,
        "confidence": round(confidence, 3),
    }


# ─────────────────────────── data load ───────────────────────────


def _load_subs(slug: str, limit: int = _MAX_ROWS):
    """Load subscriptions DataFrame + detected schema.

    Returns (df, schema_info). df has normalized cols:
        customer_id, mrr, plan, started_at, canceled_at, status

    Caller must check `schema_info["found"]`.
    """
    import pandas as pd
    from sqlalchemy import text

    info = detect_subscription_schema(slug)
    if not info["found"]:
        return pd.DataFrame(), info

    schema = _project_schema(slug)
    table = info["table"]
    cm = info["columns_map"]
    eng = _ro_engine(slug)

    # Build SELECT — only the columns we need
    select_parts: list[str] = []
    select_parts.append(f'"{cm["customer_id"]}"::text AS customer_id')
    select_parts.append(f'"{cm["mrr"]}"::numeric AS mrr')
    if cm.get("plan"):
        select_parts.append(f'"{cm["plan"]}"::text AS plan')
    else:
        select_parts.append("NULL::text AS plan")
    select_parts.append(f'"{cm["started_at"]}"::timestamptz AS started_at')
    if cm.get("canceled_at"):
        select_parts.append(f'"{cm["canceled_at"]}"::timestamptz AS canceled_at')
    else:
        select_parts.append("NULL::timestamptz AS canceled_at")
    if cm.get("status"):
        select_parts.append(f'"{cm["status"]}"::text AS status')
    else:
        select_parts.append("NULL::text AS status")

    where = ""
    if cm.get("billing_cycle"):
        # Restrict to monthly cycles when value col might be 'amount'
        where = (f' WHERE LOWER("{cm["billing_cycle"]}"::text) IN '
                 f"('monthly','month','m','mo','30','30d')")

    sql_text = (f'SELECT {", ".join(select_parts)} FROM "{schema}"."{table}"'
                f'{where} LIMIT {int(limit)}')

    try:
        df = pd.read_sql(text(sql_text), eng)
    except Exception as e:
        info_err = dict(info)
        info_err["found"] = False
        info_err["error"] = f"load failed: {e}"
        return pd.DataFrame(), info_err

    df["started_at"] = pd.to_datetime(df["started_at"], errors="coerce", utc=True).dt.tz_localize(None)
    df["canceled_at"] = pd.to_datetime(df["canceled_at"], errors="coerce", utc=True).dt.tz_localize(None)
    df["mrr"] = df["mrr"].astype(float).fillna(0.0)
    return df, info


def _to_date(d) -> date:
    if isinstance(d, date) and not isinstance(d, datetime):
        return d
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, str):
        return datetime.fromisoformat(d.replace("Z", "")).date()
    raise ValueError(f"cannot coerce to date: {d!r}")


def _month_floor(d: date) -> date:
    return date(d.year, d.month, 1)


def _add_months(d: date, n: int) -> date:
    y = d.year + (d.month - 1 + n) // 12
    m = (d.month - 1 + n) % 12 + 1
    return date(y, m, 1)


def _is_active_at(row, ref: date) -> bool:
    """Was this subscription active on `ref` date?"""
    started = row["started_at"]
    canceled = row["canceled_at"]
    if started is None or (isinstance(started, float) and started != started):
        return False
    started_d = started.date() if hasattr(started, "date") else _to_date(started)
    if started_d > ref:
        return False
    if canceled is None or (hasattr(canceled, "date") and (canceled != canceled)):
        return True
    if hasattr(canceled, "date"):
        try:
            canceled_d = canceled.date()
        except Exception:
            return True
        return canceled_d > ref
    return True


# ─────────────────────────── compute_mrr (current snapshot) ───────────────────────────


def compute_mrr(slug: str, as_of_date: str | date | None = None) -> dict[str, Any]:
    """Current MRR + ARR + active subscriber count.

    `as_of_date` defaults to today.
    """
    try:
        import pandas as pd
        df, info = _load_subs(slug)
        if not info["found"]:
            return {"ok": False, "error": "subscription schema not found",
                    "schema": info}

        ref = _to_date(as_of_date) if as_of_date else date.today()
        if df.empty:
            return {"ok": True, "mrr": 0.0, "arr": 0.0, "active_subscribers": 0,
                    "as_of": ref.isoformat(), "schema": info}

        # vectorized active-on-ref check
        ref_ts = pd.Timestamp(ref)
        started_ok = df["started_at"].notna() & (df["started_at"] <= ref_ts)
        canceled = df["canceled_at"]
        cancel_ok = canceled.isna() | (canceled > ref_ts)
        active_mask = started_ok & cancel_ok
        active = df[active_mask]

        mrr = float(active["mrr"].sum())
        arr = mrr * 12.0
        return {
            "ok": True,
            "mrr": round(mrr, 2),
            "arr": round(arr, 2),
            "active_subscribers": int(len(active)),
            "as_of": ref.isoformat(),
            "schema": {"table": info["table"], "confidence": info["confidence"]},
        }
    except Exception as e:
        logger.exception("compute_mrr failed")
        return {"ok": False, "error": str(e)}


# ─────────────────────────── MRR breakdown (the centerpiece) ───────────────────────────


def compute_mrr_breakdown(
    slug: str,
    period_start: str | date,
    period_end: str | date,
) -> dict[str, Any]:
    """Customer-level MRR breakdown across [period_start, period_end].

    Computes by walking each customer's set of subscriptions in two snapshots:

        prev_state = MRR at period_start, by customer
        curr_state = MRR at period_end + 1 day, by customer

    Then classifies each customer:

        new           : was 0, now > 0 AND first-ever subscription started in period
        reactivation  : was 0, now > 0 AND had a prior subscription before period
        churn         : was > 0, now 0
        expansion     : was > 0, now > prev (delta > 0)
        contraction   : was > 0, now < prev (delta < 0)
    """
    try:
        import pandas as pd
        df, info = _load_subs(slug)
        if not info["found"]:
            return {"ok": False, "error": "subscription schema not found",
                    "schema": info}

        ps = _to_date(period_start)
        pe = _to_date(period_end)
        if pe < ps:
            return {"ok": False, "error": "period_end must be >= period_start"}

        if df.empty:
            zero = dict.fromkeys(
                ("new_mrr", "expansion_mrr", "contraction_mrr",
                 "churn_mrr", "reactivation_mrr", "net_new_mrr",
                 "start_mrr", "end_mrr"), 0.0)
            return {"ok": True, **zero, "active_subscribers": 0,
                    "churned_subscribers": 0,
                    "period_start": ps.isoformat(),
                    "period_end": pe.isoformat(),
                    "schema": info}

        ps_ts = pd.Timestamp(ps)
        pe_ts = pd.Timestamp(pe)
        pe_next = pe_ts + pd.Timedelta(days=1)

        df["start_d"] = df["started_at"].dt.normalize()
        df["cancel_d"] = df["canceled_at"].dt.normalize()

        def _mrr_at(ref_ts):
            ok_start = df["started_at"].notna() & (df["started_at"] <= ref_ts)
            ok_cancel = df["canceled_at"].isna() | (df["canceled_at"] > ref_ts)
            active = df[ok_start & ok_cancel]
            return active.groupby("customer_id")["mrr"].sum()

        prev = _mrr_at(ps_ts)   # MRR on the morning of period_start
        curr = _mrr_at(pe_next) # MRR right after period_end

        all_customers = prev.index.union(curr.index)
        prev_full = prev.reindex(all_customers, fill_value=0.0).astype(float)
        curr_full = curr.reindex(all_customers, fill_value=0.0).astype(float)

        # First-ever start by customer (across the whole table)
        first_start = df.groupby("customer_id")["started_at"].min()
        first_start_full = first_start.reindex(all_customers)

        new_mrr = 0.0
        expansion_mrr = 0.0
        contraction_mrr = 0.0
        churn_mrr = 0.0
        reactivation_mrr = 0.0
        churned_subs = 0

        for cid in all_customers:
            p = float(prev_full.loc[cid])
            c = float(curr_full.loc[cid])
            if p <= 0 and c > 0:
                fs = first_start_full.loc[cid]
                if fs is not None and fs >= ps_ts and fs <= pe_next:
                    new_mrr += c
                else:
                    reactivation_mrr += c
            elif p > 0 and c <= 0:
                churn_mrr += p
                churned_subs += 1
            elif p > 0 and c > 0:
                delta = c - p
                if delta > 0:
                    expansion_mrr += delta
                elif delta < 0:
                    contraction_mrr += abs(delta)
            # p<=0 and c<=0 → ignore

        net_new = (new_mrr + expansion_mrr + reactivation_mrr
                   - contraction_mrr - churn_mrr)
        active_subs = int((curr_full > 0).sum())

        out = {
            "ok": True,
            "period_start": ps.isoformat(),
            "period_end": pe.isoformat(),
            "start_mrr": round(float(prev_full.sum()), 2),
            "end_mrr": round(float(curr_full.sum()), 2),
            "new_mrr": round(new_mrr, 2),
            "expansion_mrr": round(expansion_mrr, 2),
            "contraction_mrr": round(contraction_mrr, 2),
            "churn_mrr": round(churn_mrr, 2),
            "reactivation_mrr": round(reactivation_mrr, 2),
            "net_new_mrr": round(net_new, 2),
            "active_subscribers": active_subs,
            "churned_subscribers": churned_subs,
            "schema": {"table": info["table"], "confidence": info["confidence"]},
        }
        return out
    except Exception as e:
        logger.exception("compute_mrr_breakdown failed")
        return {"ok": False, "error": str(e)}


# ─────────────────────────── retention ───────────────────────────


def compute_retention(
    slug: str,
    period_start: str | date,
    period_end: str | date,
) -> dict[str, Any]:
    """Gross + net retention.

    gross = (start_mrr - churn - contraction) / start_mrr
    net   = (start_mrr - churn - contraction + expansion) / start_mrr
    """
    try:
        b = compute_mrr_breakdown(slug, period_start, period_end)
        if not b.get("ok"):
            return b
        start_mrr = b["start_mrr"]
        if start_mrr <= 0:
            return {"ok": True, "gross_retention_pct": None,
                    "net_retention_pct": None,
                    "period_start": b["period_start"],
                    "period_end": b["period_end"],
                    "note": "start_mrr is zero — retention undefined"}
        gross = (start_mrr - b["churn_mrr"] - b["contraction_mrr"]) / start_mrr
        net = (start_mrr - b["churn_mrr"] - b["contraction_mrr"]
               + b["expansion_mrr"]) / start_mrr
        return {
            "ok": True,
            "period_start": b["period_start"],
            "period_end": b["period_end"],
            "start_mrr": start_mrr,
            "gross_retention_pct": round(gross * 100, 2),
            "net_retention_pct": round(net * 100, 2),
            "components": {
                "churn_mrr": b["churn_mrr"],
                "contraction_mrr": b["contraction_mrr"],
                "expansion_mrr": b["expansion_mrr"],
            },
        }
    except Exception as e:
        logger.exception("compute_retention failed")
        return {"ok": False, "error": str(e)}


# ─────────────────────────── cohort survival ───────────────────────────


def cohort_survival(
    slug: str,
    cohort_window: str = "month",
    max_periods: int = 24,
) -> dict[str, Any]:
    """Cohort retention curve per signup-cohort.

    Returns rows = cohorts (label + size), cols = period offsets 0..max_periods-1,
    cell = % of original cohort still active at the start of that offset.
    """
    try:
        import pandas as pd
        df, info = _load_subs(slug)
        if not info["found"]:
            return {"ok": False, "error": "subscription schema not found",
                    "schema": info}
        if df.empty or cohort_window not in ("month", "week", "quarter"):
            return {"ok": True, "cohorts": [], "survival_matrix": [],
                    "max_periods": max_periods, "window": cohort_window}

        # Each customer: first signup → cohort label
        first_start = df.groupby("customer_id")["started_at"].min().reset_index()
        first_start = first_start.dropna(subset=["started_at"])

        if cohort_window == "month":
            first_start["cohort"] = first_start["started_at"].dt.to_period("M").astype(str)
            offset_unit = pd.DateOffset(months=1)
        elif cohort_window == "quarter":
            first_start["cohort"] = first_start["started_at"].dt.to_period("Q").astype(str)
            offset_unit = pd.DateOffset(months=3)
        else:  # week
            first_start["cohort"] = first_start["started_at"].dt.to_period("W").astype(str)
            offset_unit = pd.DateOffset(weeks=1)

        cohort_map = dict(zip(first_start["customer_id"], first_start["cohort"]))
        df["cohort"] = df["customer_id"].map(cohort_map)
        cohort_first_start = first_start.set_index("cohort")["started_at"].groupby(level=0).min()

        results: list[dict] = []
        survival_matrix: list[list[float | None]] = []

        for cohort_label, cohort_anchor in cohort_first_start.items():
            members = first_start[first_start["cohort"] == cohort_label]["customer_id"].tolist()
            cohort_size = len(members)
            if cohort_size == 0:
                continue
            row: list[float | None] = []
            for k in range(max_periods):
                ref_ts = cohort_anchor + (offset_unit * k)
                # active at ref_ts among cohort members
                sub = df[df["customer_id"].isin(members)]
                ok_start = sub["started_at"].notna() & (sub["started_at"] <= ref_ts)
                ok_cancel = sub["canceled_at"].isna() | (sub["canceled_at"] > ref_ts)
                still_active = sub[ok_start & ok_cancel]["customer_id"].nunique()
                pct = round(100.0 * still_active / cohort_size, 1) if cohort_size else None
                row.append(pct)
            results.append({"cohort": str(cohort_label), "size": cohort_size,
                            "anchor": str(cohort_anchor.date() if hasattr(cohort_anchor, "date") else cohort_anchor)})
            survival_matrix.append(row)

        # Limit to last 24 cohorts (UI sanity)
        if len(results) > 24:
            results = results[-24:]
            survival_matrix = survival_matrix[-24:]

        return {
            "ok": True,
            "window": cohort_window,
            "max_periods": max_periods,
            "cohorts": results,
            "survival_matrix": survival_matrix,
        }
    except Exception as e:
        logger.exception("cohort_survival failed")
        return {"ok": False, "error": str(e)}


# ─────────────────────────── trend ───────────────────────────


def mrr_trend(slug: str, months: int = 12) -> list[dict]:
    """Per-month series of MRR + breakdown components for the last `months` months.

    Returns a list of dicts (oldest first), suitable for charting. Each dict:
        {period, mrr, arr, new, expansion, churn, contraction,
         reactivation, net_new}

    On error returns an empty list rather than raising.
    """
    try:
        today = date.today()
        first_of_this = _month_floor(today)
        rows: list[dict] = []
        for k in range(months - 1, -1, -1):
            period_start = _add_months(first_of_this, -k)
            # period_end = last day of that month
            next_month = _add_months(period_start, 1)
            period_end = next_month - timedelta(days=1)
            b = compute_mrr_breakdown(slug, period_start, period_end)
            if not b.get("ok"):
                rows.append({"period": period_start.isoformat(), "mrr": None,
                             "arr": None, "error": b.get("error")})
                continue
            end_mrr = b["end_mrr"]
            rows.append({
                "period": period_start.isoformat(),
                "mrr": end_mrr,
                "arr": round(end_mrr * 12.0, 2),
                "new": b["new_mrr"],
                "expansion": b["expansion_mrr"],
                "churn": b["churn_mrr"],
                "contraction": b["contraction_mrr"],
                "reactivation": b["reactivation_mrr"],
                "net_new": b["net_new_mrr"],
                "active_subscribers": b["active_subscribers"],
            })
        return rows
    except Exception as e:
        logger.exception("mrr_trend failed")
        return [{"error": str(e)}]


__all__ = [
    "detect_subscription_schema",
    "compute_mrr",
    "compute_mrr_breakdown",
    "compute_retention",
    "cohort_survival",
    "mrr_trend",
]
