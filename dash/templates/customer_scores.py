"""Customer score precompute + cache.

Nightly cache that holds RFM + churn + CLV per customer for fast Customer 360
drill-down (<100ms). Triggered by the autonomous workflow runner via the
`__BUILTIN__:compute_customer_scores` query_template token, or on demand via
the `/customers/recompute` endpoint.

Tables:
- dash_customer_scores  (id, project_slug, customer_id, rfm_*, clv_predicted,
                         churn_risk, churn_score, days_since_last, order_count,
                         total_spend, avg_order_value, last_computed)

Skip-cooldown: 6h. Caps analysis at 50K customers per run.
Safe to call concurrently (UPSERT on (project_slug, customer_id)).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from sqlalchemy import text

logger = logging.getLogger(__name__)

_BOOTSTRAPPED = False
_COOLDOWN_HOURS = 6
_MAX_CUSTOMERS = 50_000


def _get_engine():
    from db.session import get_sql_engine as get_engine
    return get_engine()


# ─────────────────────────── Bootstrap ───────────────────────────


def bootstrap_tables() -> None:
    """Idempotent. Create dash_customer_scores + indexes."""
    global _BOOTSTRAPPED
    if _BOOTSTRAPPED:
        return
    eng = _get_engine()
    if eng is None:
        return
    ddl = [
        """
        CREATE TABLE IF NOT EXISTS dash_customer_scores (
            id SERIAL PRIMARY KEY,
            project_slug TEXT NOT NULL,
            customer_id TEXT NOT NULL,
            rfm_segment TEXT,
            rfm_r INTEGER,
            rfm_f INTEGER,
            rfm_m INTEGER,
            clv_predicted NUMERIC,
            churn_risk TEXT,
            churn_score NUMERIC,
            days_since_last NUMERIC,
            order_count INTEGER,
            total_spend NUMERIC,
            avg_order_value NUMERIC,
            last_computed TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE (project_slug, customer_id)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_cs_project ON dash_customer_scores(project_slug, last_computed DESC)",
        "CREATE INDEX IF NOT EXISTS idx_cs_segment ON dash_customer_scores(project_slug, rfm_segment)",
        "CREATE INDEX IF NOT EXISTS idx_cs_churn ON dash_customer_scores(project_slug, churn_risk)",
    ]
    try:
        with eng.begin() as cn:
            for stmt in ddl:
                cn.execute(text(stmt))
        _BOOTSTRAPPED = True
        logger.info("customer_scores table bootstrapped")
    except Exception as e:
        logger.warning("customer_scores bootstrap failed: %s", e)


# ─────────────────────────── Helpers ───────────────────────────


def _last_computed(project_slug: str) -> datetime | None:
    eng = _get_engine()
    if eng is None:
        return None
    try:
        with eng.begin() as cn:
            row = cn.execute(
                text(
                    "SELECT MAX(last_computed) FROM dash_customer_scores "
                    "WHERE project_slug = :s"
                ),
                {"s": project_slug},
            ).fetchone()
        if row and row[0]:
            return row[0]
    except Exception as e:
        logger.debug("last_computed lookup failed: %s", e)
    return None


def _detect_txn_table(project_slug: str) -> str | None:
    """Find a transactions-like table in the project schema."""
    try:
        from app.customer_360 import _resolve  # reuses same heuristic
        info = _resolve(project_slug)
        return info.get("txn_table")
    except Exception as e:
        logger.debug("txn detect via _resolve failed: %s", e)
    # Light fallback: probe known names
    eng = _get_engine()
    if eng is None:
        return None
    try:
        from dash.templates.reconcile import _project_schema_name
        schema = _project_schema_name(project_slug)
        from sqlalchemy import inspect as sa_inspect
        tables = sa_inspect(eng).get_table_names(schema=schema) or []
        lower = {t.lower(): t for t in tables}
        for c in ("transactions", "orders", "sales", "purchases", "invoices"):
            if c in lower:
                return lower[c]
    except Exception:
        pass
    return None


# ─────────────────────────── Core ───────────────────────────


def compute_and_cache(project_slug: str, force: bool = False) -> dict:
    """Compute RFM + churn + CLV per customer and UPSERT into dash_customer_scores.

    Skip if last_computed within 6h and not force.
    Returns summary dict.
    """
    bootstrap_tables()

    if not force:
        last = _last_computed(project_slug)
        if last is not None:
            try:
                age_h = (datetime.now(timezone.utc) - last.astimezone(timezone.utc)).total_seconds() / 3600.0
            except Exception:
                age_h = 999.0
            if age_h < _COOLDOWN_HOURS:
                return {
                    "ok": True,
                    "skipped": "cooldown",
                    "project_slug": project_slug,
                    "age_hours": round(age_h, 2),
                    "computed_at": last.isoformat() if last else None,
                }

    txn_table = _detect_txn_table(project_slug)
    if not txn_table:
        return {"ok": False, "project_slug": project_slug, "error": "no txn table"}

    # ── Run the 3 tools ──
    rfm_res: dict = {}
    churn_res: dict = {}
    clv_res: dict = {}
    try:
        from dash.tools.customer_intelligence import rfm_score
        rfm_res = rfm_score(project_slug, table=txn_table) or {}
    except Exception as e:
        logger.warning("rfm_score failed: %s", e)
        rfm_res = {"ok": False, "error": str(e)}

    try:
        from dash.tools.clv_churn import churn_risk_score, clv_score
        churn_res = churn_risk_score(project_slug, table=txn_table) or {}
        clv_res = clv_score(project_slug, table=txn_table) or {}
    except Exception as e:
        logger.warning("clv/churn failed: %s", e)
        if not churn_res:
            churn_res = {"ok": False, "error": str(e)}
        if not clv_res:
            clv_res = {"ok": False, "error": str(e)}

    if not (rfm_res.get("ok") or churn_res.get("ok") or clv_res.get("ok")):
        return {
            "ok": False,
            "project_slug": project_slug,
            "error": "all scoring tools failed",
            "rfm_error": rfm_res.get("error"),
            "churn_error": churn_res.get("error"),
            "clv_error": clv_res.get("error"),
        }

    # ── Merge by customer_id ──
    merged: dict[str, dict] = {}

    # RFM (top customers — keyed by 'customer')
    if rfm_res.get("ok"):
        for c in rfm_res.get("top_customers") or []:
            cid = str(c.get("customer") or "")
            if not cid:
                continue
            rfm = str(c.get("rfm") or "")
            entry = merged.setdefault(cid, {})
            entry["rfm_segment"] = c.get("segment")
            if len(rfm) >= 3 and rfm[:3].isdigit():
                entry["rfm_r"] = int(rfm[0])
                entry["rfm_f"] = int(rfm[1])
                entry["rfm_m"] = int(rfm[2])
            entry["order_count"] = int(c.get("frequency") or 0)
            entry["total_spend"] = float(c.get("monetary") or 0.0)
            entry["days_since_last"] = float(c.get("recency_days") or 0)
            oc = entry.get("order_count") or 0
            ts = entry.get("total_spend") or 0.0
            entry["avg_order_value"] = (ts / oc) if oc else 0.0

    # Churn (high_risk_customers — keyed by 'customer_id')
    if churn_res.get("ok"):
        for c in churn_res.get("high_risk_customers") or []:
            cid = str(c.get("customer_id") or "")
            if not cid:
                continue
            entry = merged.setdefault(cid, {})
            entry["churn_risk"] = c.get("category")
            entry["churn_score"] = float(c.get("score") or 0.0)
            entry["days_since_last"] = float(c.get("days_since") or entry.get("days_since_last") or 0)
            if "order_count" not in entry:
                entry["order_count"] = int(c.get("frequency") or 0)
            if "total_spend" not in entry and c.get("lifetime_value") is not None:
                entry["total_spend"] = float(c.get("lifetime_value") or 0.0)

    # CLV (top_clv_customers — keyed by 'customer_id')
    if clv_res.get("ok"):
        for c in clv_res.get("top_clv_customers") or []:
            cid = str(c.get("customer_id") or "")
            if not cid:
                continue
            entry = merged.setdefault(cid, {})
            entry["clv_predicted"] = float(c.get("predicted_clv") or 0.0)
            if "order_count" not in entry:
                entry["order_count"] = int(c.get("frequency") or 0)
            if "total_spend" not in entry and c.get("total_revenue") is not None:
                entry["total_spend"] = float(c.get("total_revenue") or 0.0)
            if "avg_order_value" not in entry and c.get("avg_order") is not None:
                entry["avg_order_value"] = float(c.get("avg_order") or 0.0)

    # Default churn_risk for active-tier customers not in high_risk list
    if churn_res.get("ok"):
        for cid, entry in merged.items():
            if "churn_risk" not in entry:
                entry["churn_risk"] = "active"
                entry["churn_score"] = 0.0

    # Cap (memory safety)
    if len(merged) > _MAX_CUSTOMERS:
        # keep highest spenders
        items = sorted(merged.items(),
                       key=lambda kv: float(kv[1].get("total_spend") or 0.0),
                       reverse=True)[:_MAX_CUSTOMERS]
        merged = dict(items)

    # ── UPSERT ──
    eng = _get_engine()
    if eng is None:
        return {"ok": False, "project_slug": project_slug, "error": "no engine"}

    upsert_sql = text(
        """
        INSERT INTO dash_customer_scores
          (project_slug, customer_id, rfm_segment, rfm_r, rfm_f, rfm_m,
           clv_predicted, churn_risk, churn_score, days_since_last,
           order_count, total_spend, avg_order_value, last_computed)
        VALUES
          (:slug, :cid, :seg, :r, :f, :m,
           :clv, :risk, :cscore, :days,
           :oc, :spend, :aov, NOW())
        ON CONFLICT (project_slug, customer_id) DO UPDATE SET
           rfm_segment = EXCLUDED.rfm_segment,
           rfm_r = EXCLUDED.rfm_r,
           rfm_f = EXCLUDED.rfm_f,
           rfm_m = EXCLUDED.rfm_m,
           clv_predicted = EXCLUDED.clv_predicted,
           churn_risk = EXCLUDED.churn_risk,
           churn_score = EXCLUDED.churn_score,
           days_since_last = EXCLUDED.days_since_last,
           order_count = EXCLUDED.order_count,
           total_spend = EXCLUDED.total_spend,
           avg_order_value = EXCLUDED.avg_order_value,
           last_computed = NOW()
        """
    )

    scored = 0
    seg_breakdown: dict[str, int] = {}
    risk_breakdown: dict[str, int] = {}

    try:
        with eng.begin() as cn:
            for cid, entry in merged.items():
                params = {
                    "slug": project_slug,
                    "cid": cid,
                    "seg": entry.get("rfm_segment"),
                    "r": entry.get("rfm_r"),
                    "f": entry.get("rfm_f"),
                    "m": entry.get("rfm_m"),
                    "clv": entry.get("clv_predicted"),
                    "risk": entry.get("churn_risk"),
                    "cscore": entry.get("churn_score"),
                    "days": entry.get("days_since_last"),
                    "oc": entry.get("order_count"),
                    "spend": entry.get("total_spend"),
                    "aov": entry.get("avg_order_value"),
                }
                try:
                    cn.execute(upsert_sql, params)
                    scored += 1
                    seg = entry.get("rfm_segment") or "Unknown"
                    seg_breakdown[seg] = seg_breakdown.get(seg, 0) + 1
                    risk = entry.get("churn_risk") or "unknown"
                    risk_breakdown[risk] = risk_breakdown.get(risk, 0) + 1
                except Exception as e:
                    logger.debug("upsert skipped for %s: %s", cid, e)
    except Exception as e:
        logger.exception("compute_and_cache UPSERT failed")
        return {"ok": False, "project_slug": project_slug, "error": str(e)}

    # Augment seg breakdown with full rfm distribution if available
    if rfm_res.get("ok"):
        for k, v in (rfm_res.get("segments") or {}).items():
            seg_breakdown.setdefault(k, int(v))

    # Augment risk breakdown with full distribution if available
    if churn_res.get("ok"):
        for k, v in (churn_res.get("distribution") or {}).items():
            # full distribution authoritative
            risk_breakdown[k] = int(v)

    return {
        "ok": True,
        "project_slug": project_slug,
        "scored": scored,
        "txn_table": txn_table,
        "segments_breakdown": seg_breakdown,
        "risk_breakdown": risk_breakdown,
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }


# ─────────────────────────── Read helpers ───────────────────────────


_SELECT_COLS = (
    "id, project_slug, customer_id, rfm_segment, rfm_r, rfm_f, rfm_m, "
    "clv_predicted, churn_risk, churn_score, days_since_last, "
    "order_count, total_spend, avg_order_value, last_computed"
)


def _row_to_dict(r) -> dict:
    d = dict(r._mapping) if hasattr(r, "_mapping") else dict(r)
    # Coerce decimals/timestamps
    for k in ("clv_predicted", "churn_score", "days_since_last", "total_spend", "avg_order_value"):
        if d.get(k) is not None:
            try:
                d[k] = float(d[k])
            except Exception:
                pass
    if d.get("last_computed") is not None:
        try:
            d["last_computed"] = d["last_computed"].isoformat()
        except Exception:
            d["last_computed"] = str(d["last_computed"])
    return d


def get_customer_score(project_slug: str, customer_id: str) -> dict | None:
    bootstrap_tables()
    eng = _get_engine()
    if eng is None:
        return None
    try:
        with eng.begin() as cn:
            row = cn.execute(
                text(
                    f"SELECT {_SELECT_COLS} FROM dash_customer_scores "
                    "WHERE project_slug = :s AND customer_id = :c LIMIT 1"
                ),
                {"s": project_slug, "c": str(customer_id)},
            ).fetchone()
    except Exception as e:
        logger.debug("get_customer_score failed: %s", e)
        return None
    if not row:
        return None
    return _row_to_dict(row)


def top_by_segment(project_slug: str, segment: str, limit: int = 50) -> list[dict]:
    bootstrap_tables()
    eng = _get_engine()
    if eng is None:
        return []
    limit = max(1, min(int(limit), 500))
    try:
        with eng.begin() as cn:
            rows = cn.execute(
                text(
                    f"SELECT {_SELECT_COLS} FROM dash_customer_scores "
                    "WHERE project_slug = :s AND rfm_segment = :seg "
                    "ORDER BY total_spend DESC NULLS LAST LIMIT :n"
                ),
                {"s": project_slug, "seg": segment, "n": limit},
            ).fetchall()
    except Exception as e:
        logger.debug("top_by_segment failed: %s", e)
        return []
    return [_row_to_dict(r) for r in rows]


def top_at_risk(project_slug: str, limit: int = 50) -> list[dict]:
    bootstrap_tables()
    eng = _get_engine()
    if eng is None:
        return []
    limit = max(1, min(int(limit), 500))
    try:
        with eng.begin() as cn:
            rows = cn.execute(
                text(
                    f"SELECT {_SELECT_COLS} FROM dash_customer_scores "
                    "WHERE project_slug = :s AND churn_risk IN ('at_risk', 'churned') "
                    "ORDER BY total_spend DESC NULLS LAST LIMIT :n"
                ),
                {"s": project_slug, "n": limit},
            ).fetchall()
    except Exception as e:
        logger.debug("top_at_risk failed: %s", e)
        return []
    return [_row_to_dict(r) for r in rows]


def aggregate_segments(project_slug: str) -> list[dict]:
    """Cache-only segments roll-up. Empty list if cache empty."""
    bootstrap_tables()
    eng = _get_engine()
    if eng is None:
        return []
    try:
        with eng.begin() as cn:
            rows = cn.execute(
                text(
                    """
                    SELECT rfm_segment AS name,
                           COUNT(*)::int AS count,
                           COALESCE(AVG(total_spend), 0) AS avg_spend,
                           COALESCE(SUM(total_spend), 0) AS total_revenue
                    FROM dash_customer_scores
                    WHERE project_slug = :s AND rfm_segment IS NOT NULL
                    GROUP BY rfm_segment
                    ORDER BY count DESC
                    """
                ),
                {"s": project_slug},
            ).fetchall()
    except Exception as e:
        logger.debug("aggregate_segments failed: %s", e)
        return []
    out: list[dict] = []
    for r in rows:
        d = dict(r._mapping) if hasattr(r, "_mapping") else dict(zip(["name", "count", "avg_spend", "total_revenue"], r))
        out.append({
            "name": d.get("name") or "Unknown",
            "count": int(d.get("count") or 0),
            "avg_spend": round(float(d.get("avg_spend") or 0.0), 2),
            "total_revenue": round(float(d.get("total_revenue") or 0.0), 2),
        })
    return out


def aggregate_health(project_slug: str) -> dict:
    """Cache-only churn/health roll-up. Empty distribution if cache empty."""
    bootstrap_tables()
    eng = _get_engine()
    if eng is None:
        return {"risk_distribution": {}, "top_at_risk": []}
    dist: dict[str, int] = {"active": 0, "cooling": 0, "at_risk": 0, "churned": 0}
    try:
        with eng.begin() as cn:
            rows = cn.execute(
                text(
                    """
                    SELECT churn_risk, COUNT(*)::int
                    FROM dash_customer_scores
                    WHERE project_slug = :s AND churn_risk IS NOT NULL
                    GROUP BY churn_risk
                    """
                ),
                {"s": project_slug},
            ).fetchall()
        for r in rows:
            dist[str(r[0])] = int(r[1])
    except Exception as e:
        logger.debug("aggregate_health failed: %s", e)
    return {
        "risk_distribution": dist,
        "top_at_risk": top_at_risk(project_slug, limit=10),
    }


# ─────────────────────────── Cleanup ───────────────────────────


def purge_stale(days: int = 90) -> int:
    bootstrap_tables()
    eng = _get_engine()
    if eng is None:
        return 0
    days = max(1, int(days))
    try:
        with eng.begin() as cn:
            res = cn.execute(
                text(
                    "DELETE FROM dash_customer_scores "
                    "WHERE last_computed < NOW() - (:d || ' days')::interval"
                ),
                {"d": str(days)},
            )
            return int(res.rowcount or 0)
    except Exception as e:
        logger.warning("purge_stale failed: %s", e)
        return 0
