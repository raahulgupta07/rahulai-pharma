"""Customer 360° drill page — single-customer aggregation API.

Endpoints:
- GET  /api/projects/{slug}/customers/list                       search/list customers
- GET  /api/projects/{slug}/customers/segments-summary           RFM segment rollup (cached)
- GET  /api/projects/{slug}/customers/health-summary             churn risk rollup (cached)
- GET  /api/projects/{slug}/customers/{customer_id}              full 360 detail
- GET  /api/projects/{slug}/customers/{customer_id}/timeline     chronological events
- GET  /api/projects/{slug}/customers/{customer_id}/note         list admin notes
- POST /api/projects/{slug}/customers/{customer_id}/note         add admin note

All endpoints require viewer-or-higher permission. Response cap ~6 KB.
Auto-detects customer table + transactions table + key columns.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any
from fastapi import APIRouter, HTTPException, Request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["Customer360"])


# ─────────────────── auth helpers ───────────────────

def _user(request: Request) -> dict:
    from app.auth import get_current_user
    u = get_current_user(request)
    if not u:
        raise HTTPException(401, "auth required")
    return u


def _check(user: dict, slug: str, role: str = "viewer") -> None:
    from app.auth import check_project_permission
    res = check_project_permission(user, slug, role)
    if not res:
        # super-admin bypass: check_project_permission returns dict for super-admin owner
        from app.auth import SUPER_ADMIN
        if user.get("username") != SUPER_ADMIN:
            raise HTTPException(403, "permission denied")


# ─────────────────── candidate lists ───────────────────

_CUSTOMER_TABLE_CANDIDATES = ["customers", "customer", "accounts", "account", "users", "members", "clients"]
_TXN_TABLE_CANDIDATES = ["transactions", "orders", "sales", "tickets", "txn", "purchases", "invoices", "order_items"]

_CUSTOMER_ID_HINTS = ["customer_id", "user_id", "account_id", "cust_id", "client_id", "buyer_id", "member_id"]
_NAME_HINTS = ["name", "customer_name", "full_name", "display_name", "first_name"]
_TIER_HINTS = ["tier", "segment", "membership", "loyalty_tier", "level"]
_JOINED_HINTS = ["joined_at", "created_at", "registered_at", "signup_date", "first_seen"]

_DATE_HINTS = ["ts", "order_date", "purchase_date", "created_at", "txn_time", "sale_date", "date", "timestamp", "transaction_date"]
_AMOUNT_HINTS = ["amount", "total", "revenue", "net_sales", "sale_amount", "order_total", "price", "spend", "grand_total", "subtotal"]
_ORDER_ID_HINTS = ["order_id", "transaction_id", "txn_id", "basket_id", "invoice_id", "ticket_id", "id"]
_QTY_HINTS = ["qty", "quantity", "items", "units", "item_count"]
_SKU_HINTS = ["sku_id", "sku", "product_id", "item_id", "upc", "product"]
_CATEGORY_HINTS = ["category", "category_name", "dept", "department", "product_category", "type"]


# ─────────────────── module-level cache ───────────────────

_CACHE: dict[str, tuple[float, Any]] = {}
_CACHE_TTL_DEFAULT = 30.0
_CACHE_TTL_SUMMARY = 60.0


def _cache_get(key: str) -> Any:
    e = _CACHE.get(key)
    if not e:
        return None
    ts, val = e
    if time.time() - ts > _CACHE_TTL_DEFAULT:
        _CACHE.pop(key, None)
        return None
    return val


def _cache_put(key: str, val: Any) -> None:
    _CACHE[key] = (time.time(), val)


def _cache_get_long(key: str, ttl: float = _CACHE_TTL_SUMMARY) -> Any:
    e = _CACHE.get(key)
    if not e:
        return None
    ts, val = e
    if time.time() - ts > ttl:
        _CACHE.pop(key, None)
        return None
    return val


# ─────────────────── bootstrap notes table ───────────────────

def _bootstrap() -> None:
    try:
        from db.session import get_write_engine
        from sqlalchemy import text as _t
        eng = get_write_engine()
        if eng is None:
            return
        with eng.begin() as cn:
            cn.execute(_t("""
                CREATE TABLE IF NOT EXISTS dash_customer_notes (
                    id SERIAL PRIMARY KEY,
                    project_slug TEXT NOT NULL,
                    customer_id TEXT NOT NULL,
                    note TEXT NOT NULL,
                    author TEXT,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """))
            cn.execute(_t("""
                CREATE INDEX IF NOT EXISTS idx_customer_notes_proj_cust
                ON dash_customer_notes(project_slug, customer_id, created_at DESC)
            """))
    except Exception as e:
        logger.warning(f"customer_360 bootstrap failed: {e}")


_bootstrap()


# ─────────────────── schema introspection helpers ───────────────────

def _schema(slug: str) -> str:
    from dash.templates.reconcile import _project_schema_name
    return _project_schema_name(slug)


def _list_tables(eng, schema: str) -> list[str]:
    from sqlalchemy import inspect as sa_inspect
    try:
        return sa_inspect(eng).get_table_names(schema=schema) or []
    except Exception:
        return []


def _list_columns(eng, schema: str, table: str) -> list[str]:
    from sqlalchemy import inspect as sa_inspect
    try:
        return [c["name"] for c in sa_inspect(eng).get_columns(table, schema=schema)]
    except Exception:
        return []


def _pick_col(cols: list[str], hints: list[str]) -> str | None:
    lower = {c.lower(): c for c in cols}
    for h in hints:
        if h in lower:
            return lower[h]
    for h in hints:
        for lc, real in lower.items():
            if h in lc:
                return real
    return None


def _detect_customer_table(eng, schema: str) -> tuple[str | None, dict]:
    """Find a customer master table. Returns (table, col_map) or (None, {})."""
    tables = _list_tables(eng, schema)
    lower_set = {t.lower(): t for t in tables}
    for cand in _CUSTOMER_TABLE_CANDIDATES:
        if cand in lower_set:
            t = lower_set[cand]
            cols = _list_columns(eng, schema, t)
            cid = _pick_col(cols, _CUSTOMER_ID_HINTS)
            if cid:
                return t, {
                    "customer_id": cid,
                    "name": _pick_col(cols, _NAME_HINTS),
                    "tier": _pick_col(cols, _TIER_HINTS),
                    "joined_at": _pick_col(cols, _JOINED_HINTS),
                    "all_cols": cols,
                }
    return None, {}


def _detect_txn_table(eng, schema: str) -> tuple[str | None, dict]:
    """Find a transactions table with customer_id + date + amount. Returns (table, col_map)."""
    tables = _list_tables(eng, schema)
    lower_set = {t.lower(): t for t in tables}

    def _evaluate(t: str) -> dict | None:
        cols = _list_columns(eng, schema, t)
        cid = _pick_col(cols, _CUSTOMER_ID_HINTS)
        dt = _pick_col(cols, _DATE_HINTS)
        amt = _pick_col(cols, _AMOUNT_HINTS)
        if cid and dt:
            return {
                "table": t,
                "customer_id": cid,
                "date": dt,
                "amount": amt,
                "order_id": _pick_col(cols, _ORDER_ID_HINTS),
                "qty": _pick_col(cols, _QTY_HINTS),
                "sku": _pick_col(cols, _SKU_HINTS),
                "category": _pick_col(cols, _CATEGORY_HINTS),
                "all_cols": cols,
            }
        return None

    for cand in _TXN_TABLE_CANDIDATES:
        if cand in lower_set:
            r = _evaluate(lower_set[cand])
            if r:
                return r["table"], r

    # Generic fallback: any table with customer_id + date
    for t in tables:
        r = _evaluate(t)
        if r:
            return r["table"], r
    return None, {}


def _resolve(slug: str) -> dict:
    """Single resolver — engine + schema + customer + txn tables. Raises 404 if not found."""
    from db.session import get_sql_engine
    eng = get_sql_engine()
    if eng is None:
        raise HTTPException(503, "no engine")
    schema = _schema(slug)
    cust_t, cust_map = _detect_customer_table(eng, schema)
    txn_t, txn_map = _detect_txn_table(eng, schema)
    if not txn_t:
        raise HTTPException(404, "no transactions-style table found in project schema")
    return {
        "engine": eng, "schema": schema,
        "customer_table": cust_t, "customer_map": cust_map,
        "txn_table": txn_t, "txn_map": txn_map,
    }


def _q(name: str) -> str:
    return f'"{name}"'


def _trim(payload: Any, max_bytes: int = 6000) -> Any:
    """Cap response size; truncate lists if too big."""
    try:
        b = json.dumps(payload, default=str).encode()
        if len(b) <= max_bytes:
            return payload
        # try shrinking purchase_history / timeline / category_mix etc
        if isinstance(payload, dict):
            for k in ("purchase_history", "timeline", "category_mix", "top_skus", "monthly_spend",
                      "recommendations", "customers", "high_risk_customers", "top_at_risk"):
                if k in payload and isinstance(payload[k], list):
                    while len(payload[k]) > 5 and len(json.dumps(payload, default=str).encode()) > max_bytes:
                        payload[k] = payload[k][: max(5, len(payload[k]) // 2)]
        if isinstance(payload, list):
            while len(payload) > 5 and len(json.dumps(payload, default=str).encode()) > max_bytes:
                payload = payload[: max(5, len(payload) // 2)]
        return payload
    except Exception:
        return payload


# ─────────────────── /list ───────────────────

@router.get("/projects/{slug}/customers/list")
def list_customers(
    slug: str,
    request: Request,
    q: str = "",
    limit: int = 50,
    order_by: str = "spend",
):
    user = _user(request)
    _check(user, slug, "viewer")
    from sqlalchemy import text as _t

    info = _resolve(slug)
    eng, schema = info["engine"], info["schema"]
    txn_t = info["txn_table"]
    tm = info["txn_map"]
    cust_t = info["customer_table"]
    cm = info["customer_map"]

    cust_col = tm["customer_id"]
    date_col = tm["date"]
    amt_col = tm.get("amount")
    name_col = cm.get("name") if cm else None

    limit = max(1, min(int(limit), 200))
    order_clause = "total_spend DESC NULLS LAST"
    if order_by == "recency":
        order_clause = "last_seen DESC NULLS LAST"
    elif order_by == "frequency":
        order_clause = "order_count DESC NULLS LAST"

    name_select = ""
    name_join = ""
    if cust_t and name_col:
        name_select = f', cust.{_q(name_col)} AS name'
        name_join = f' LEFT JOIN "{schema}".{_q(cust_t)} cust ON cust.{_q(cm["customer_id"])}::text = t.{_q(cust_col)}::text '

    amt_expr = "0"
    if amt_col:
        amt_expr = (f'COALESCE(SUM(CASE WHEN t.{_q(amt_col)}::text ~ \'^-?[0-9.]+$\' '
                    f'THEN t.{_q(amt_col)}::numeric ELSE 0 END), 0)')

    where_q = ""
    params: dict[str, Any] = {"lim": limit}
    if q:
        params["q"] = f"%{q.lower()}%"
        if cust_t and name_col:
            where_q = (f' WHERE LOWER(t.{_q(cust_col)}::text) LIKE :q '
                       f'OR LOWER(cust.{_q(name_col)}::text) LIKE :q')
        else:
            where_q = f' WHERE LOWER(t.{_q(cust_col)}::text) LIKE :q'

    sql = f"""
        SELECT t.{_q(cust_col)}::text AS customer_id{name_select},
               {amt_expr} AS total_spend,
               COUNT(*)::int AS order_count,
               MAX(t.{_q(date_col)}::text) AS last_seen
        FROM "{schema}".{_q(txn_t)} t
        {name_join}
        {where_q}
        GROUP BY t.{_q(cust_col)}{', cust.' + _q(name_col) if name_col and cust_t else ''}
        ORDER BY {order_clause}
        LIMIT :lim
    """

    customers: list[dict] = []
    total_count = 0
    try:
        with eng.begin() as cn:
            rows = cn.execute(_t(sql), params).fetchall()
            now_row = cn.execute(_t(
                f'SELECT MAX({_q(date_col)}::text) FROM "{schema}".{_q(txn_t)}'
            )).fetchone()
            total_row = cn.execute(_t(
                f'SELECT COUNT(DISTINCT {_q(cust_col)})::int FROM "{schema}".{_q(txn_t)}'
            )).fetchone()
            total_count = int(total_row[0]) if total_row and total_row[0] is not None else 0
        ref = now_row[0] if now_row and now_row[0] else None
        from datetime import datetime
        ref_dt = None
        if ref:
            try:
                ref_dt = datetime.fromisoformat(ref[:19].replace("T", " "))
            except Exception:
                ref_dt = None
        for r in rows:
            d = dict(r._mapping) if hasattr(r, "_mapping") else dict(zip(r.keys(), r))
            last = d.get("last_seen")
            days_since = None
            if last and ref_dt:
                try:
                    last_dt = datetime.fromisoformat(str(last)[:19].replace("T", " "))
                    days_since = (ref_dt - last_dt).days
                except Exception:
                    pass
            customers.append({
                "id": d.get("customer_id"),
                "name": d.get("name"),
                "total_spend": float(d.get("total_spend") or 0.0),
                "order_count": int(d.get("order_count") or 0),
                "last_seen": str(last) if last else None,
                "days_since": days_since,
            })
    except Exception as e:
        logger.exception("list_customers failed")
        raise HTTPException(500, f"query failed: {e}")

    return _trim({"customers": customers, "total_count": total_count}, max_bytes=6000)


# ─────────────────── /{customer_id} (360 detail) ───────────────────

def _safe_div(a: float, b: float) -> float:
    return a / b if b else 0.0


@router.get("/projects/{slug}/customers/{customer_id}")
def get_customer_detail(slug: str, customer_id: str, request: Request):
    user = _user(request)
    _check(user, slug, "viewer")
    cache_key = f"detail:{slug}:{customer_id}"
    cached = _cache_get(cache_key)
    if cached:
        return cached

    from sqlalchemy import text as _t
    info = _resolve(slug)
    eng, schema = info["engine"], info["schema"]
    txn_t, tm = info["txn_table"], info["txn_map"]
    cust_t, cm = info["customer_table"], info["customer_map"]

    cust_col, date_col = tm["customer_id"], tm["date"]
    amt_col = tm.get("amount")
    order_id_col = tm.get("order_id")
    qty_col = tm.get("qty")
    sku_col = tm.get("sku")
    cat_col = tm.get("category")

    profile: dict = {"name": None, "tier": None, "joined_at": None}
    if cust_t and cm:
        try:
            with eng.begin() as cn:
                row = cn.execute(_t(
                    f'SELECT * FROM "{schema}".{_q(cust_t)} '
                    f'WHERE {_q(cm["customer_id"])}::text = :cid LIMIT 1'
                ), {"cid": customer_id}).fetchone()
            if row:
                d = dict(row._mapping) if hasattr(row, "_mapping") else {}
                if cm.get("name"):
                    profile["name"] = d.get(cm["name"])
                if cm.get("tier"):
                    profile["tier"] = d.get(cm["tier"])
                if cm.get("joined_at"):
                    v = d.get(cm["joined_at"])
                    profile["joined_at"] = str(v) if v else None
                # include up to 5 extra small fields
                extras = 0
                for k, v in d.items():
                    if k in (cm.get("name"), cm.get("tier"), cm.get("joined_at"), cm["customer_id"]):
                        continue
                    if v is None:
                        continue
                    sv = str(v)
                    if len(sv) > 80:
                        continue
                    profile[k] = sv
                    extras += 1
                    if extras >= 5:
                        break
        except Exception as e:
            logger.warning(f"profile lookup failed: {e}")

    # Stats
    amt_sum_expr = "0"
    if amt_col:
        amt_sum_expr = (f'SUM(CASE WHEN {_q(amt_col)}::text ~ \'^-?[0-9.]+$\' '
                        f'THEN {_q(amt_col)}::numeric ELSE 0 END)')
    stats: dict = {
        "first_purchase": None, "last_purchase": None, "tenure_days": 0,
        "lifetime_value": 0.0, "order_count": 0, "avg_order_value": 0.0,
        "days_since_last_order": None, "median_inter_order_gap": None,
    }
    purchase_history: list[dict] = []
    monthly_spend: list[dict] = []
    category_mix: list[dict] = []
    top_skus: list[dict] = []

    try:
        with eng.begin() as cn:
            stats_sql = f"""
                SELECT MIN({_q(date_col)}::text) AS first_p,
                       MAX({_q(date_col)}::text) AS last_p,
                       COUNT(*)::int AS n_orders,
                       COALESCE({amt_sum_expr}, 0) AS ltv,
                       (SELECT MAX({_q(date_col)}::text) FROM "{schema}".{_q(txn_t)}) AS ref_date
                FROM "{schema}".{_q(txn_t)}
                WHERE {_q(cust_col)}::text = :cid
            """
            row = cn.execute(_t(stats_sql), {"cid": customer_id}).fetchone()
            if not row or not row[2]:
                raise HTTPException(404, f"customer '{customer_id}' has no transactions")
            from datetime import datetime
            first_p = row[0]; last_p = row[1]; n = int(row[2]); ltv = float(row[3] or 0.0); ref = row[4]

            def _parse(s):
                if not s:
                    return None
                try:
                    return datetime.fromisoformat(str(s)[:19].replace("T", " "))
                except Exception:
                    return None

            f_dt = _parse(first_p); l_dt = _parse(last_p); r_dt = _parse(ref)
            tenure = (l_dt - f_dt).days if f_dt and l_dt else 0
            days_since = (r_dt - l_dt).days if r_dt and l_dt else None

            stats.update({
                "first_purchase": str(first_p) if first_p else None,
                "last_purchase": str(last_p) if last_p else None,
                "tenure_days": int(tenure),
                "lifetime_value": round(ltv, 2),
                "order_count": n,
                "avg_order_value": round(_safe_div(ltv, n), 2),
                "days_since_last_order": days_since,
            })

            # median inter-order gap
            try:
                gaps_sql = f"""
                    SELECT EXTRACT(EPOCH FROM ({_q(date_col)}::timestamp - LAG({_q(date_col)}::timestamp)
                        OVER (ORDER BY {_q(date_col)})))/86400.0 AS gap_days
                    FROM "{schema}".{_q(txn_t)}
                    WHERE {_q(cust_col)}::text = :cid
                """
                gap_rows = cn.execute(_t(gaps_sql), {"cid": customer_id}).fetchall()
                gaps = sorted([float(g[0]) for g in gap_rows if g[0] is not None and float(g[0]) > 0])
                if gaps:
                    mid = len(gaps) // 2
                    median = gaps[mid] if len(gaps) % 2 else (gaps[mid - 1] + gaps[mid]) / 2
                    stats["median_inter_order_gap"] = round(median, 2)
            except Exception:
                pass

            # purchase history (last 50)
            ph_cols_extra = []
            if order_id_col:
                ph_cols_extra.append(f'{_q(order_id_col)}::text AS order_id')
            if qty_col:
                ph_cols_extra.append(f'{_q(qty_col)}::text AS items')
            if amt_col:
                ph_cols_extra.append(f'CASE WHEN {_q(amt_col)}::text ~ \'^-?[0-9.]+$\' '
                                     f'THEN {_q(amt_col)}::numeric ELSE 0 END AS amount')
            extra_sql = (", " + ", ".join(ph_cols_extra)) if ph_cols_extra else ""
            ph_sql = f"""
                SELECT {_q(date_col)}::text AS dt {extra_sql}
                FROM "{schema}".{_q(txn_t)}
                WHERE {_q(cust_col)}::text = :cid
                ORDER BY {_q(date_col)} DESC
                LIMIT 50
            """
            ph_rows = cn.execute(_t(ph_sql), {"cid": customer_id}).fetchall()
            for r in ph_rows:
                d = dict(r._mapping) if hasattr(r, "_mapping") else {}
                purchase_history.append({
                    "date": d.get("dt"),
                    "order_id": d.get("order_id"),
                    "items": int(float(d["items"])) if d.get("items") and str(d["items"]).replace(".", "").lstrip("-").isdigit() else (d.get("items") if d.get("items") else None),
                    "amount": round(float(d.get("amount") or 0), 2) if d.get("amount") is not None else None,
                })

            # monthly spend (last 12)
            if amt_col:
                ms_sql = f"""
                    SELECT TO_CHAR(DATE_TRUNC('month', {_q(date_col)}::timestamp), 'YYYY-MM') AS m,
                           COALESCE({amt_sum_expr}, 0) AS amt,
                           COUNT(*)::int AS n
                    FROM "{schema}".{_q(txn_t)}
                    WHERE {_q(cust_col)}::text = :cid
                      AND {_q(date_col)}::timestamp >= NOW() - INTERVAL '13 months'
                    GROUP BY 1
                    ORDER BY 1 DESC
                    LIMIT 12
                """
                try:
                    ms_rows = cn.execute(_t(ms_sql), {"cid": customer_id}).fetchall()
                    for r in ms_rows:
                        monthly_spend.append({
                            "month": r[0],
                            "amount": round(float(r[1] or 0), 2),
                            "orders": int(r[2] or 0),
                        })
                    monthly_spend.reverse()
                except Exception as e:
                    logger.debug(f"monthly_spend skipped: {e}")

            # category mix
            if cat_col and amt_col:
                cm_sql = f"""
                    SELECT {_q(cat_col)}::text AS cat, COALESCE({amt_sum_expr}, 0) AS amt
                    FROM "{schema}".{_q(txn_t)}
                    WHERE {_q(cust_col)}::text = :cid
                    GROUP BY {_q(cat_col)}
                    ORDER BY amt DESC
                    LIMIT 10
                """
                try:
                    cat_rows = cn.execute(_t(cm_sql), {"cid": customer_id}).fetchall()
                    total_cat = sum(float(r[1] or 0) for r in cat_rows) or 1.0
                    for r in cat_rows:
                        amt = float(r[1] or 0)
                        category_mix.append({
                            "category": r[0] or "(unknown)",
                            "spend": round(amt, 2),
                            "share_pct": round(amt / total_cat * 100, 1),
                        })
                except Exception as e:
                    logger.debug(f"category_mix skipped: {e}")

            # top SKUs
            if sku_col:
                qty_sum = "COUNT(*)::int" if not qty_col else (
                    f'SUM(CASE WHEN {_q(qty_col)}::text ~ \'^-?[0-9.]+$\' '
                    f'THEN {_q(qty_col)}::numeric ELSE 1 END)::int'
                )
                amt_sum_or_zero = amt_sum_expr if amt_col else "0"
                ts_sql = f"""
                    SELECT {_q(sku_col)}::text AS sku,
                           {qty_sum} AS qty,
                           COALESCE({amt_sum_or_zero}, 0) AS amt
                    FROM "{schema}".{_q(txn_t)}
                    WHERE {_q(cust_col)}::text = :cid
                    GROUP BY {_q(sku_col)}
                    ORDER BY amt DESC
                    LIMIT 10
                """
                try:
                    sku_rows = cn.execute(_t(ts_sql), {"cid": customer_id}).fetchall()
                    for r in sku_rows:
                        top_skus.append({
                            "sku": r[0] or "(unknown)",
                            "qty": int(r[1] or 0),
                            "spend": round(float(r[2] or 0), 2),
                        })
                except Exception as e:
                    logger.debug(f"top_skus skipped: {e}")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("customer detail core failed")
        raise HTTPException(500, f"detail failed: {e}")

    # ── Cache hit path (precomputed RFM + churn + CLV) ──
    cached_score: dict | None = None
    try:
        from dash.templates.customer_scores import get_customer_score
        cached_score = get_customer_score(slug, customer_id)
    except Exception as e:
        logger.debug(f"customer_scores cache lookup skipped: {e}")

    # RFM
    rfm_block: dict = {"r": None, "f": None, "m": None, "segment": None}
    if cached_score and cached_score.get("rfm_segment"):
        rfm_block = {
            "r": cached_score.get("rfm_r"),
            "f": cached_score.get("rfm_f"),
            "m": cached_score.get("rfm_m"),
            "segment": cached_score.get("rfm_segment"),
        }
    else:
        try:
            from dash.tools.customer_intelligence import rfm_score
            rfm_res = rfm_score(slug, table=txn_t)
            if rfm_res.get("ok"):
                for c in rfm_res.get("top_customers", []):
                    if str(c.get("customer")) == str(customer_id):
                        rfm = c.get("rfm") or ""
                        if len(rfm) == 3:
                            rfm_block = {
                                "r": int(rfm[0]), "f": int(rfm[1]), "m": int(rfm[2]),
                                "segment": c.get("segment"),
                            }
                        break
        except Exception as e:
            logger.debug(f"rfm lookup skipped: {e}")

    # Churn block — prefer cache, else derive from days_since vs median gap
    churn_block: dict = {"risk": "active", "score": 0.0, "days_since": stats["days_since_last_order"]}
    if cached_score and cached_score.get("churn_risk"):
        churn_block = {
            "risk": cached_score.get("churn_risk"),
            "score": float(cached_score.get("churn_score") or 0.0),
            "days_since": stats["days_since_last_order"],
        }
    if not (cached_score and cached_score.get("churn_risk")):
        try:
            ds = stats["days_since_last_order"] or 0
            median_gap = stats["median_inter_order_gap"] or 30
            threshold = max(median_gap * 2.0, 60.0)
            score = ds / threshold if threshold else 0.0
            if score < 0.5:
                risk = "active"
            elif score < 1.0:
                risk = "cooling"
            elif score < 2.0:
                risk = "at_risk"
            else:
                risk = "churned"
            churn_block = {"risk": risk, "score": round(float(score), 2), "days_since": ds}
        except Exception:
            pass

    # CLV — prefer cache
    clv_predicted = None
    if cached_score and cached_score.get("clv_predicted") is not None:
        try:
            clv_predicted = float(cached_score["clv_predicted"])
        except Exception:
            clv_predicted = None
    if clv_predicted is None:
        try:
            from dash.tools.clv_churn import clv_score
            clv_res = clv_score(slug, table=txn_t)
            if clv_res.get("ok"):
                for c in clv_res.get("top_clv_customers", []):
                    if str(c.get("customer_id")) == str(customer_id):
                        clv_predicted = float(c.get("predicted_clv") or 0.0)
                        break
                if clv_predicted is None:
                    # fallback: simple proxy
                    tenure = max(1, stats["tenure_days"])
                    rate = stats["order_count"] / tenure
                    aov = stats["avg_order_value"]
                    clv_predicted = round(rate * 365 * aov, 2)
        except Exception as e:
            logger.debug(f"clv lookup skipped: {e}")

    # Recommendations
    recommendations: list[dict] = []
    if sku_col:
        try:
            from dash.tools.recommendations import next_best_offer
            rec_res = next_best_offer(slug, customer_id, table=txn_t,
                                      customer_col=cust_col, sku_col=sku_col, top_n=5)
            if rec_res.get("ok"):
                recommendations = rec_res.get("recommendations") or []
        except Exception as e:
            logger.debug(f"recommendations skipped: {e}")

    # ── Subscription status (Tier 4 MRR) ─────────────────────────
    subscription_status: dict | None = None
    try:
        from dash.tools.mrr_analytics import detect_subscription_schema, _project_schema, _ro_engine
        from sqlalchemy import text as _sqltext
        sinfo = detect_subscription_schema(slug)
        if sinfo.get("found"):
            stbl = sinfo["table"]; sm = sinfo["columns_map"]
            sschema = _project_schema(slug)
            seng = _ro_engine(slug)
            cust_c = sm.get("customer_id"); mrr_c = sm.get("mrr")
            plan_c = sm.get("plan"); started_c = sm.get("started_at")
            cancel_c = sm.get("canceled_at"); status_c = sm.get("status")
            if cust_c and mrr_c and started_c:
                # Active subscriptions for this customer (canceled_at IS NULL or in future)
                cancel_cond = (f' AND ("{cancel_c}" IS NULL OR "{cancel_c}" > NOW())'
                               if cancel_c else "")
                cols = [f'"{mrr_c}"::numeric AS mrr',
                        f'"{started_c}"::timestamptz AS started_at']
                if plan_c:
                    cols.append(f'"{plan_c}"::text AS plan')
                if status_c:
                    cols.append(f'"{status_c}"::text AS status')
                sub_sql = (f'SELECT {", ".join(cols)} FROM "{sschema}"."{stbl}" '
                           f'WHERE "{cust_c}"::text = :cid{cancel_cond} '
                           f'ORDER BY "{started_c}" ASC LIMIT 50')
                with seng.begin() as scn:
                    sub_rows = scn.execute(_sqltext(sub_sql), {"cid": str(customer_id)}).mappings().all()
                # Expansion history: all subs (incl canceled) chronologically
                hist_cols = [f'"{mrr_c}"::numeric AS mrr',
                             f'"{started_c}"::timestamptz AS started_at']
                if cancel_c:
                    hist_cols.append(f'"{cancel_c}"::timestamptz AS canceled_at')
                if plan_c:
                    hist_cols.append(f'"{plan_c}"::text AS plan')
                hist_sql = (f'SELECT {", ".join(hist_cols)} FROM "{sschema}"."{stbl}" '
                            f'WHERE "{cust_c}"::text = :cid '
                            f'ORDER BY "{started_c}" ASC LIMIT 50')
                with seng.begin() as scn:
                    hist_rows = scn.execute(_sqltext(hist_sql), {"cid": str(customer_id)}).mappings().all()
                current_mrr = sum(float(r.get("mrr") or 0) for r in sub_rows)
                first_started = (sub_rows[0]["started_at"] if sub_rows
                                 else (hist_rows[0]["started_at"] if hist_rows else None))
                plan_label = None
                if sub_rows and "plan" in sub_rows[0]:
                    plan_label = sub_rows[0].get("plan")
                expansion_history = [
                    {
                        "mrr": float(r.get("mrr") or 0),
                        "started_at": str(r.get("started_at")) if r.get("started_at") else None,
                        "canceled_at": str(r.get("canceled_at")) if r.get("canceled_at") else None,
                        "plan": r.get("plan"),
                    }
                    for r in hist_rows
                ]
                subscription_status = {
                    "current_mrr": round(current_mrr, 2),
                    "active_subscriptions": len(sub_rows),
                    "plan": plan_label,
                    "started_at": str(first_started) if first_started else None,
                    "expansion_history": expansion_history[:20],
                }
    except Exception as e:
        logger.debug(f"subscription_status lookup skipped: {e}")

    payload = {
        "customer_id": customer_id,
        "profile": profile,
        "stats": stats,
        "rfm": rfm_block,
        "churn": churn_block,
        "clv_predicted": clv_predicted,
        "subscription_status": subscription_status,
        "purchase_history": purchase_history,
        "category_mix": category_mix,
        "top_skus": top_skus,
        "monthly_spend": monthly_spend,
        "recommendations": recommendations,
    }
    payload = _trim(payload, max_bytes=6000)
    _cache_put(cache_key, payload)
    return payload


# ─────────────────── /{customer_id}/timeline ───────────────────

@router.get("/projects/{slug}/customers/{customer_id}/timeline")
def get_customer_timeline(slug: str, customer_id: str, request: Request, limit: int = 200):
    user = _user(request)
    _check(user, slug, "viewer")

    from sqlalchemy import text as _t
    info = _resolve(slug)
    eng, schema = info["engine"], info["schema"]
    txn_t, tm = info["txn_table"], info["txn_map"]

    cust_col, date_col = tm["customer_id"], tm["date"]
    amt_col = tm.get("amount")
    order_id_col = tm.get("order_id")
    limit = max(1, min(int(limit), 500))

    events: list[dict] = []
    extra = ""
    if order_id_col:
        extra += f', {_q(order_id_col)}::text AS oid'
    if amt_col:
        extra += (f', CASE WHEN {_q(amt_col)}::text ~ \'^-?[0-9.]+$\' '
                  f'THEN {_q(amt_col)}::numeric ELSE 0 END AS amt')

    sql = f"""
        SELECT {_q(date_col)}::text AS ts {extra}
        FROM "{schema}".{_q(txn_t)}
        WHERE {_q(cust_col)}::text = :cid
        ORDER BY {_q(date_col)} DESC
        LIMIT :lim
    """
    try:
        with eng.begin() as cn:
            rows = cn.execute(_t(sql), {"cid": customer_id, "lim": limit}).fetchall()
        for r in rows:
            d = dict(r._mapping) if hasattr(r, "_mapping") else {}
            amt = float(d.get("amt") or 0) if "amt" in d else None
            kind = "order"
            label = f"Order {d.get('oid')}" if d.get("oid") else "Purchase"
            if amt is not None and amt < 0:
                kind = "return"
                label = f"Return {d.get('oid')}" if d.get("oid") else "Return"
            events.append({
                "ts": d.get("ts"),
                "kind": kind,
                "label": label,
                "amount": round(amt, 2) if amt is not None else None,
                "meta": {"order_id": d.get("oid")} if d.get("oid") else {},
            })
    except Exception as e:
        logger.exception("timeline failed")
        raise HTTPException(500, f"timeline failed: {e}")

    # Optional: support tickets if a "tickets" or "support_tickets" table exists with this customer
    try:
        for cand in ("support_tickets", "tickets"):
            tabs_lower = {t.lower(): t for t in _list_tables(eng, schema)}
            if cand in tabs_lower and cand != txn_t:
                tt = tabs_lower[cand]
                tcols = _list_columns(eng, schema, tt)
                tcid = _pick_col(tcols, _CUSTOMER_ID_HINTS)
                tdt = _pick_col(tcols, _DATE_HINTS)
                if tcid and tdt:
                    with eng.begin() as cn:
                        srows = cn.execute(_t(
                            f'SELECT {_q(tdt)}::text AS ts FROM "{schema}".{_q(tt)} '
                            f'WHERE {_q(tcid)}::text = :cid ORDER BY {_q(tdt)} DESC LIMIT 30'
                        ), {"cid": customer_id}).fetchall()
                    for sr in srows:
                        events.append({
                            "ts": sr[0],
                            "kind": "support",
                            "label": "Support ticket",
                            "amount": None,
                            "meta": {"source_table": tt},
                        })
                break
    except Exception:
        pass

    events.sort(key=lambda x: str(x.get("ts") or ""), reverse=True)
    events = events[:limit]
    return _trim(events, max_bytes=6000)


# ─────────────────── /{customer_id}/note ───────────────────

@router.get("/projects/{slug}/customers/{customer_id}/note")
def list_customer_notes(slug: str, customer_id: str, request: Request):
    user = _user(request)
    _check(user, slug, "viewer")
    from db.session import get_sql_engine
    from sqlalchemy import text as _t
    eng = get_sql_engine()
    if eng is None:
        return {"notes": []}
    try:
        with eng.begin() as cn:
            rows = cn.execute(_t(
                "SELECT id, note, author, created_at FROM dash_customer_notes "
                "WHERE project_slug = :s AND customer_id = :c "
                "ORDER BY created_at DESC LIMIT 50"
            ), {"s": slug, "c": customer_id}).fetchall()
    except Exception as e:
        logger.warning(f"note list failed: {e}")
        return {"notes": []}
    notes = [{
        "id": r[0], "note": r[1], "author": r[2],
        "created_at": r[3].isoformat() if r[3] else None,
    } for r in rows]
    return _trim({"notes": notes}, max_bytes=6000)


@router.post("/projects/{slug}/customers/{customer_id}/note")
async def add_customer_note(slug: str, customer_id: str, request: Request):
    user = _user(request)
    _check(user, slug, "viewer")
    body = await request.json() or {}
    note = (body.get("note") or "").strip()
    if not note:
        raise HTTPException(400, "note required")
    if len(note) > 2000:
        note = note[:2000]
    from db.session import get_write_engine
    from sqlalchemy import text as _t
    eng = get_write_engine()
    if eng is None:
        raise HTTPException(503, "no engine")
    author = user.get("username") or str(user.get("user_id") or "")
    try:
        with eng.begin() as cn:
            row = cn.execute(_t(
                "INSERT INTO dash_customer_notes (project_slug, customer_id, note, author) "
                "VALUES (:s, :c, :n, :a) RETURNING id, created_at"
            ), {"s": slug, "c": customer_id, "n": note, "a": author}).fetchone()
    except Exception as e:
        logger.exception("note insert failed")
        raise HTTPException(500, f"insert failed: {e}")
    return {
        "ok": True, "id": row[0],
        "created_at": row[1].isoformat() if row[1] else None,
        "author": author, "note": note,
    }


# ─────────────────── /segments-summary ───────────────────

@router.get("/projects/{slug}/customer-segments-summary")
def segments_summary(slug: str, request: Request):
    user = _user(request)
    _check(user, slug, "viewer")
    cache_key = f"segments:{slug}"
    cached = _cache_get_long(cache_key, ttl=60.0)
    if cached:
        return cached

    # Try cache first (precomputed nightly).
    try:
        from dash.templates.customer_scores import aggregate_segments
        cached_segs = aggregate_segments(slug)
        if cached_segs:
            payload = {"segments": cached_segs, "source": "cache"}
            _CACHE[cache_key] = (time.time(), payload)
            return _trim(payload, max_bytes=6000)
    except Exception as e:
        logger.debug(f"segments cache miss: {e}")

    info = _resolve(slug)
    txn_t = info["txn_table"]
    try:
        from dash.tools.customer_intelligence import rfm_score
        res = rfm_score(slug, table=txn_t)
    except Exception as e:
        logger.exception("rfm in segments-summary failed")
        raise HTTPException(500, f"rfm failed: {e}")
    if not res.get("ok"):
        return {"segments": [], "error": res.get("error")}

    seg_counts = res.get("segments") or {}
    # We don't have per-segment avg_spend / total_revenue cheap from rfm_score.
    # Approximate via top_customers grouping for the labels available.
    top = res.get("top_customers") or []
    by_seg: dict[str, dict[str, float]] = {}
    for c in top:
        seg = c.get("segment") or "Unknown"
        b = by_seg.setdefault(seg, {"sum": 0.0, "n": 0})
        b["sum"] += float(c.get("monetary") or 0)
        b["n"] += 1

    segments = []
    for name, count in sorted(seg_counts.items(), key=lambda x: -x[1]):
        b = by_seg.get(name, {"sum": 0.0, "n": 0})
        avg = (b["sum"] / b["n"]) if b["n"] else 0.0
        segments.append({
            "name": name,
            "count": int(count),
            "avg_spend": round(avg, 2),
            "total_revenue": round(b["sum"], 2),
        })
    payload = {"segments": segments}
    _CACHE[cache_key] = (time.time(), payload)
    return _trim(payload, max_bytes=6000)


# ─────────────────── /health-summary ───────────────────

@router.get("/projects/{slug}/customer-health-summary")
def health_summary(slug: str, request: Request):
    user = _user(request)
    _check(user, slug, "viewer")
    cache_key = f"health:{slug}"
    cached = _cache_get_long(cache_key, ttl=60.0)
    if cached:
        return cached

    # Try cache first (precomputed nightly).
    try:
        from dash.templates.customer_scores import aggregate_health
        cached_h = aggregate_health(slug)
        if cached_h and any(int(v) > 0 for v in (cached_h.get("risk_distribution") or {}).values()):
            cached_h["source"] = "cache"
            _CACHE[cache_key] = (time.time(), cached_h)
            return _trim(cached_h, max_bytes=6000)
    except Exception as e:
        logger.debug(f"health cache miss: {e}")

    info = _resolve(slug)
    txn_t = info["txn_table"]
    try:
        from dash.tools.clv_churn import churn_risk_score
        res = churn_risk_score(slug, table=txn_t)
    except Exception as e:
        logger.exception("churn in health-summary failed")
        raise HTTPException(500, f"churn failed: {e}")
    if not res.get("ok"):
        return {"risk_distribution": {"active": 0, "cooling": 0, "at_risk": 0, "churned": 0},
                "top_at_risk": [], "error": res.get("error")}

    payload = {
        "risk_distribution": res.get("distribution") or {},
        "top_at_risk": (res.get("high_risk_customers") or [])[:10],
        "median_inter_order_gap": res.get("median_inter_order_gap"),
    }
    _CACHE[cache_key] = (time.time(), payload)
    return _trim(payload, max_bytes=6000)


# ─────────────────── /customers/recompute ───────────────────

@router.post("/projects/{slug}/customer-recompute")
def recompute_scores(slug: str, request: Request):
    """Admin/editor: force-recompute the customer-score cache for this project."""
    user = _user(request)
    _check(user, slug, "editor")
    try:
        from dash.templates.customer_scores import compute_and_cache
    except Exception as e:
        raise HTTPException(500, f"customer_scores import failed: {e}")
    try:
        result = compute_and_cache(slug, force=True)
    except Exception as e:
        logger.exception("recompute_scores failed")
        raise HTTPException(500, f"recompute failed: {e}")
    # Bust the in-memory caches so the next read sees fresh values.
    for k in (f"segments:{slug}", f"health:{slug}"):
        _CACHE.pop(k, None)
    return result


# ─────────────────── /customer-spend-trends ───────────────────

@router.get("/projects/{slug}/customer-spend-trends")
def spend_trends(slug: str, request: Request, ids: str = "", weeks: int = 12):
    """Per-customer last-N-weeks spend timeline. Returns {trends: {customer_id: [w0,...,wN-1]}}."""
    user = _user(request)
    _check(user, slug, "viewer")
    if not ids:
        return {"trends": {}}
    id_list = [x.strip() for x in ids.split(",") if x.strip()][:200]
    if not id_list:
        return {"trends": {}}
    weeks = max(1, min(int(weeks), 52))
    cache_key = f"trends:{slug}:{weeks}:{hash(tuple(id_list))}"
    cached = _cache_get(cache_key)
    if cached:
        return cached
    try:
        from db.session import get_sql_engine
        from sqlalchemy import text as _t
        eng = get_sql_engine()
        if eng is None:
            return {"trends": {}}
        info = _resolve(slug)
        txm = info.get("txn_map") or {}
        t = info.get("txn_table")
        cid = txm.get("customer_id"); dt = txm.get("date"); amt = txm.get("amount")
        if not t or not cid or not dt or not amt:
            return {"trends": {}, "note": "missing txn columns"}
        sch = info["schema"]
        # Build per-customer weekly buckets via DATE_TRUNC.
        sql = f'''
            WITH wks AS (
              SELECT generate_series(0, :wk - 1) AS i
            ), boundaries AS (
              SELECT i, date_trunc('week', NOW()) - (i || ' weeks')::interval AS week_start FROM wks
            )
            SELECT "{cid}"::text AS cust,
                   date_trunc('week', "{dt}") AS wk,
                   SUM("{amt}"::numeric)::numeric AS spend
            FROM "{sch}"."{t}"
            WHERE "{cid}"::text = ANY(:ids)
              AND "{dt}" >= NOW() - (:wk * 7 || ' days')::interval
            GROUP BY 1, 2
        '''
        from datetime import datetime, timedelta, timezone
        with eng.begin() as cn:
            rows = cn.execute(_t(sql), {"ids": id_list, "wk": weeks}).fetchall()
        # Build week-start anchors (oldest → newest), align by index
        now = datetime.now(timezone.utc)
        # Monday of current week (date_trunc('week') uses ISO Monday)
        day_anchor = now - timedelta(days=now.weekday())
        anchors = [(day_anchor - timedelta(weeks=(weeks - 1 - i))) for i in range(weeks)]
        # Map (anchor.date_iso) → index
        idx_for = {a.date().isoformat(): i for i, a in enumerate(anchors)}
        trends: dict[str, list[float]] = {cid_v: [0.0] * weeks for cid_v in id_list}
        for cust, wk, spend in rows:
            try:
                key = wk.date().isoformat() if hasattr(wk, "date") else str(wk)[:10]
                i = idx_for.get(key)
                if i is None:
                    continue
                if cust in trends:
                    trends[cust][i] = float(spend or 0)
            except Exception:
                continue
        # Drop all-zero entries to keep payload small.
        trends = {k: v for k, v in trends.items() if any(x > 0 for x in v)}
        payload = {"trends": trends, "weeks": weeks}
        _cache_put(cache_key, payload)
        return payload
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("spend_trends failed")
        return {"trends": {}, "error": str(e)[:200]}
