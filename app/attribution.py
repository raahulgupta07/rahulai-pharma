"""Multi-Touch Attribution (MTA) API — Tier 4.

Endpoints:
- POST /api/projects/{slug}/touchpoints              single ingest
- POST /api/projects/{slug}/touchpoints/bulk         bulk ingest (max 1000)
- POST /api/projects/{slug}/conversions              register conversion (auto-attribute)
- POST /api/projects/{slug}/conversions/bulk         bulk
- POST /api/projects/{slug}/attribution/run          re-run attribution for window
- GET  /api/projects/{slug}/attribution/by-channel   aggregated channel credits
- GET  /api/projects/{slug}/attribution/by-campaign  aggregated campaign credits
- GET  /api/projects/{slug}/attribution/customer/{customer_id}  full journey

All queries are parameterized and per-project filtered (RLS via project_slug).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import text as _t

from dash.attribution.engine import (
    ALLOWED_MODELS,
    attribute_all_pending,
    attribute_conversion,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["Attribution"])

ALLOWED_CHANNELS = {
    "email", "sms", "ad", "organic", "direct", "social", "campaign", "push", "referral",
}
ALLOWED_EVENT_TYPES = {"click", "open", "view", "visit", "impression"}
BULK_MAX = 1000


# ── Auth helpers (mirror app/campaigns.py) ────────────────────────────────


def _user(request: Request) -> dict:
    from app.auth import get_current_user
    u = get_current_user(request)
    if not u:
        raise HTTPException(401, "auth required")
    return u


def _require_role(user: dict, slug: str, role: str) -> dict:
    from app.auth import check_project_permission
    proj = check_project_permission(user, slug, role)
    if not proj:
        raise HTTPException(403, f"{role} role required for project '{slug}'")
    return proj


def _viewer(user: dict, slug: str) -> dict:
    return _require_role(user, slug, "viewer")


def _editor(user: dict, slug: str) -> dict:
    return _require_role(user, slug, "editor")


def _engine():
    from db.session import get_sql_engine
    eng = get_sql_engine()
    if eng is None:
        raise HTTPException(503, "db engine unavailable")
    return eng


# ── Bootstrap (idempotent) ──


_BOOTSTRAP_SQL = [
    """
    CREATE TABLE IF NOT EXISTS dash_touchpoints (
      id BIGSERIAL PRIMARY KEY,
      project_slug TEXT NOT NULL,
      customer_id TEXT NOT NULL,
      channel TEXT NOT NULL,
      campaign_id BIGINT,
      event_type TEXT NOT NULL,
      event_at TIMESTAMPTZ NOT NULL,
      metadata JSONB DEFAULT '{}'::jsonb,
      created_at TIMESTAMPTZ DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_tp_slug_cust_time ON dash_touchpoints(project_slug, customer_id, event_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_tp_campaign ON dash_touchpoints(campaign_id)",
    """
    CREATE TABLE IF NOT EXISTS dash_conversions (
      id BIGSERIAL PRIMARY KEY,
      project_slug TEXT NOT NULL,
      customer_id TEXT NOT NULL,
      transaction_id TEXT,
      revenue NUMERIC,
      converted_at TIMESTAMPTZ NOT NULL,
      metadata JSONB,
      created_at TIMESTAMPTZ DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_conv_slug_cust_time ON dash_conversions(project_slug, customer_id, converted_at DESC)",
    """
    CREATE TABLE IF NOT EXISTS dash_attribution_credits (
      id BIGSERIAL PRIMARY KEY,
      project_slug TEXT NOT NULL,
      conversion_id BIGINT NOT NULL,
      touchpoint_id BIGINT NOT NULL,
      model TEXT NOT NULL,
      credit NUMERIC NOT NULL,
      credited_revenue NUMERIC,
      computed_at TIMESTAMPTZ DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_attr_slug_conv ON dash_attribution_credits(project_slug, conversion_id)",
    "CREATE INDEX IF NOT EXISTS idx_attr_tp_model ON dash_attribution_credits(touchpoint_id, model)",
]


def _bootstrap_tables() -> None:
    try:
        from db.session import get_write_engine
        eng = get_write_engine()
        if eng is None:
            logger.warning("attribution: no engine for bootstrap")
            return
        with eng.begin() as cn:
            for stmt in _BOOTSTRAP_SQL:
                try:
                    cn.execute(_t(stmt))
                except Exception as e:
                    logger.warning("attribution bootstrap stmt failed: %s", e)
    except Exception:
        logger.exception("attribution bootstrap failed")


try:
    _bootstrap_tables()
except Exception:
    logger.exception("attribution bootstrap top-level failure")


# ── Utility ─────────────────────────────────────────────────────────────


def _parse_dt(v: Any) -> Optional[datetime]:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    s = str(v).strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        d = datetime.fromisoformat(s)
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _to_iso(v: Any) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, str):
        return v
    try:
        return v.isoformat()
    except Exception:
        return str(v)


def _validate_touchpoint(body: dict) -> dict:
    cid = (body.get("customer_id") or "").strip()
    if not cid:
        raise HTTPException(400, "customer_id required")
    channel = (body.get("channel") or "").strip().lower()
    if channel not in ALLOWED_CHANNELS:
        raise HTTPException(400, f"invalid channel '{channel}'; allowed {sorted(ALLOWED_CHANNELS)}")
    event_type = (body.get("event_type") or "").strip().lower()
    if event_type not in ALLOWED_EVENT_TYPES:
        raise HTTPException(400, f"invalid event_type '{event_type}'")
    event_at = _parse_dt(body.get("event_at"))
    if event_at is None:
        raise HTTPException(400, "event_at required (ISO timestamp)")
    campaign_id = body.get("campaign_id")
    if campaign_id is not None:
        try:
            campaign_id = int(campaign_id)
        except Exception:
            raise HTTPException(400, "campaign_id must be int or null")
    metadata = body.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise HTTPException(400, "metadata must be a JSON object")
    return {
        "customer_id": cid[:200],
        "channel": channel,
        "campaign_id": campaign_id,
        "event_type": event_type,
        "event_at": event_at,
        "metadata": metadata,
    }


def _validate_conversion(body: dict) -> dict:
    cid = (body.get("customer_id") or "").strip()
    if not cid:
        raise HTTPException(400, "customer_id required")
    converted_at = _parse_dt(body.get("converted_at"))
    if converted_at is None:
        raise HTTPException(400, "converted_at required (ISO timestamp)")
    revenue = body.get("revenue")
    if revenue is not None:
        try:
            revenue = float(revenue)
        except Exception:
            raise HTTPException(400, "revenue must be numeric")
    transaction_id = body.get("transaction_id")
    if transaction_id is not None:
        transaction_id = str(transaction_id)[:200]
    metadata = body.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise HTTPException(400, "metadata must be a JSON object")
    return {
        "customer_id": cid[:200],
        "transaction_id": transaction_id,
        "revenue": revenue,
        "converted_at": converted_at,
        "metadata": metadata,
    }


# ── Touchpoints ─────────────────────────────────────────────────────────


@router.post("/projects/{slug}/touchpoints")
async def ingest_touchpoint(slug: str, request: Request):
    user = _user(request)
    _editor(user, slug)
    body = await request.json() or {}
    tp = _validate_touchpoint(body)
    eng = _engine()
    with eng.begin() as cn:
        row = cn.execute(
            _t(
                "INSERT INTO dash_touchpoints "
                "(project_slug, customer_id, channel, campaign_id, event_type, event_at, metadata) "
                "VALUES (:slug, :cid, :ch, :camp, :et, :ea, CAST(:meta AS jsonb)) "
                "RETURNING id"
            ),
            {
                "slug": slug,
                "cid": tp["customer_id"],
                "ch": tp["channel"],
                "camp": tp["campaign_id"],
                "et": tp["event_type"],
                "ea": tp["event_at"],
                "meta": json.dumps(tp["metadata"]),
            },
        ).fetchone()
    return {"ok": True, "id": int(row[0])}


@router.post("/projects/{slug}/touchpoints/bulk")
async def ingest_touchpoints_bulk(slug: str, request: Request):
    user = _user(request)
    _editor(user, slug)
    body = await request.json()
    if not isinstance(body, list):
        raise HTTPException(400, "expected a JSON array")
    if len(body) > BULK_MAX:
        raise HTTPException(400, f"max {BULK_MAX} per request")
    validated = [_validate_touchpoint(b) for b in body]
    eng = _engine()
    inserted_ids: list[int] = []
    with eng.begin() as cn:
        for tp in validated:
            row = cn.execute(
                _t(
                    "INSERT INTO dash_touchpoints "
                    "(project_slug, customer_id, channel, campaign_id, event_type, event_at, metadata) "
                    "VALUES (:slug, :cid, :ch, :camp, :et, :ea, CAST(:meta AS jsonb)) "
                    "RETURNING id"
                ),
                {
                    "slug": slug,
                    "cid": tp["customer_id"],
                    "ch": tp["channel"],
                    "camp": tp["campaign_id"],
                    "et": tp["event_type"],
                    "ea": tp["event_at"],
                    "meta": json.dumps(tp["metadata"]),
                },
            ).fetchone()
            inserted_ids.append(int(row[0]))
    return {"ok": True, "count": len(inserted_ids), "ids": inserted_ids}


# ── Conversions ─────────────────────────────────────────────────────────


@router.post("/projects/{slug}/conversions")
async def ingest_conversion(slug: str, request: Request):
    user = _user(request)
    _editor(user, slug)
    body = await request.json() or {}
    conv = _validate_conversion(body)
    eng = _engine()
    with eng.begin() as cn:
        row = cn.execute(
            _t(
                "INSERT INTO dash_conversions "
                "(project_slug, customer_id, transaction_id, revenue, converted_at, metadata) "
                "VALUES (:slug, :cid, :tx, :rev, :ca, CAST(:meta AS jsonb)) "
                "RETURNING id"
            ),
            {
                "slug": slug,
                "cid": conv["customer_id"],
                "tx": conv["transaction_id"],
                "rev": conv["revenue"],
                "ca": conv["converted_at"],
                "meta": json.dumps(conv["metadata"]),
            },
        ).fetchone()
        conversion_id = int(row[0])
    # Auto-attribute with linear (fast, deterministic).
    try:
        attribute_conversion(slug, conversion_id, model="linear", lookback_days=30)
    except Exception:
        logger.exception("auto-attribute failed for conversion %s", conversion_id)
    return {"ok": True, "id": conversion_id}


@router.post("/projects/{slug}/conversions/bulk")
async def ingest_conversions_bulk(slug: str, request: Request):
    user = _user(request)
    _editor(user, slug)
    body = await request.json()
    if not isinstance(body, list):
        raise HTTPException(400, "expected a JSON array")
    if len(body) > BULK_MAX:
        raise HTTPException(400, f"max {BULK_MAX} per request")
    validated = [_validate_conversion(b) for b in body]
    eng = _engine()
    inserted_ids: list[int] = []
    with eng.begin() as cn:
        for conv in validated:
            row = cn.execute(
                _t(
                    "INSERT INTO dash_conversions "
                    "(project_slug, customer_id, transaction_id, revenue, converted_at, metadata) "
                    "VALUES (:slug, :cid, :tx, :rev, :ca, CAST(:meta AS jsonb)) "
                    "RETURNING id"
                ),
                {
                    "slug": slug,
                    "cid": conv["customer_id"],
                    "tx": conv["transaction_id"],
                    "rev": conv["revenue"],
                    "ca": conv["converted_at"],
                    "meta": json.dumps(conv["metadata"]),
                },
            ).fetchone()
            inserted_ids.append(int(row[0]))
    # Auto-attribute (linear) for each — best-effort, fail-soft.
    for cid in inserted_ids:
        try:
            attribute_conversion(slug, cid, model="linear", lookback_days=30)
        except Exception:
            logger.exception("bulk auto-attribute failed for %s", cid)
    return {"ok": True, "count": len(inserted_ids), "ids": inserted_ids}


# ── Attribution run ─────────────────────────────────────────────────────


@router.post("/projects/{slug}/attribution/run")
def run_attribution(
    slug: str,
    request: Request,
    model: str = "linear",
    days: int = 30,
):
    user = _user(request)
    _editor(user, slug)
    if model not in ALLOWED_MODELS:
        raise HTTPException(400, f"invalid model '{model}'; allowed {sorted(ALLOWED_MODELS)}")
    days = max(1, min(int(days or 30), 365))
    res = attribute_all_pending(slug, model=model, since_days=days, lookback_days=days)
    return res


# ── Aggregations ────────────────────────────────────────────────────────


def _validate_model(model: str) -> str:
    m = (model or "linear").strip().lower()
    if m not in ALLOWED_MODELS:
        raise HTTPException(400, f"invalid model '{m}'")
    return m


@router.get("/projects/{slug}/attribution/by-channel")
def attribution_by_channel(slug: str, request: Request, model: str = "linear", days: int = 30):
    user = _user(request)
    _viewer(user, slug)
    m = _validate_model(model)
    days = max(1, min(int(days or 30), 365))
    eng = _engine()
    sql = """
        SELECT t.channel,
               SUM(a.credit) AS total_credit,
               COALESCE(SUM(a.credited_revenue), 0) AS credited_revenue,
               COUNT(DISTINCT a.conversion_id) AS conversions,
               COUNT(DISTINCT a.touchpoint_id) AS touchpoints
        FROM dash_attribution_credits a
        JOIN dash_touchpoints t ON t.id = a.touchpoint_id
        JOIN dash_conversions c ON c.id = a.conversion_id
        WHERE a.project_slug = :slug
          AND a.model = :model
          AND c.converted_at >= NOW() - (:days || ' days')::interval
        GROUP BY t.channel
        ORDER BY credited_revenue DESC
    """
    with eng.begin() as cn:
        rows = cn.execute(_t(sql), {"slug": slug, "model": m, "days": str(days)}).fetchall()
    total_credit = sum(float(r[1] or 0) for r in rows) or 1.0
    return [
        {
            "channel": r[0],
            "credit_share": float(r[1] or 0) / total_credit,
            "credit": float(r[1] or 0),
            "credited_revenue": float(r[2] or 0),
            "conversions": int(r[3] or 0),
            "touchpoints": int(r[4] or 0),
        }
        for r in rows
    ]


@router.get("/projects/{slug}/attribution/by-campaign")
def attribution_by_campaign(slug: str, request: Request, model: str = "linear", days: int = 30):
    user = _user(request)
    _viewer(user, slug)
    m = _validate_model(model)
    days = max(1, min(int(days or 30), 365))
    eng = _engine()
    sql = """
        SELECT t.campaign_id,
               COALESCE(camp.name, '(none)') AS campaign_name,
               SUM(a.credit) AS total_credit,
               COALESCE(SUM(a.credited_revenue), 0) AS credited_revenue,
               COUNT(DISTINCT a.conversion_id) AS conversions,
               COUNT(DISTINCT a.touchpoint_id) AS touchpoints
        FROM dash_attribution_credits a
        JOIN dash_touchpoints t ON t.id = a.touchpoint_id
        JOIN dash_conversions c ON c.id = a.conversion_id
        LEFT JOIN dash_campaigns camp ON camp.id = t.campaign_id
        WHERE a.project_slug = :slug
          AND a.model = :model
          AND c.converted_at >= NOW() - (:days || ' days')::interval
        GROUP BY t.campaign_id, camp.name
        ORDER BY credited_revenue DESC
    """
    with eng.begin() as cn:
        rows = cn.execute(_t(sql), {"slug": slug, "model": m, "days": str(days)}).fetchall()
    total_credit = sum(float(r[2] or 0) for r in rows) or 1.0
    return [
        {
            "campaign_id": int(r[0]) if r[0] is not None else None,
            "campaign_name": r[1],
            "credit_share": float(r[2] or 0) / total_credit,
            "credit": float(r[2] or 0),
            "credited_revenue": float(r[3] or 0),
            "conversions": int(r[4] or 0),
            "touchpoints": int(r[5] or 0),
        }
        for r in rows
    ]


@router.get("/projects/{slug}/attribution/customer/{customer_id}")
def attribution_customer_journey(slug: str, customer_id: str, request: Request, days: int = 90):
    user = _user(request)
    _viewer(user, slug)
    days = max(1, min(int(days or 90), 730))
    eng = _engine()
    with eng.begin() as cn:
        tps = cn.execute(
            _t(
                "SELECT id, channel, campaign_id, event_type, event_at, metadata "
                "FROM dash_touchpoints "
                "WHERE project_slug=:slug AND customer_id=:cid "
                "AND event_at >= NOW() - (:days || ' days')::interval "
                "ORDER BY event_at ASC LIMIT 500"
            ),
            {"slug": slug, "cid": customer_id, "days": str(days)},
        ).fetchall()
        convs = cn.execute(
            _t(
                "SELECT id, transaction_id, revenue, converted_at, metadata "
                "FROM dash_conversions "
                "WHERE project_slug=:slug AND customer_id=:cid "
                "AND converted_at >= NOW() - (:days || ' days')::interval "
                "ORDER BY converted_at ASC"
            ),
            {"slug": slug, "cid": customer_id, "days": str(days)},
        ).fetchall()
        conv_ids = [int(r[0]) for r in convs]
        credits_rows = []
        if conv_ids:
            credits_rows = cn.execute(
                _t(
                    "SELECT conversion_id, touchpoint_id, model, credit, credited_revenue "
                    "FROM dash_attribution_credits "
                    "WHERE project_slug=:slug AND conversion_id = ANY(:ids)"
                ),
                {"slug": slug, "ids": conv_ids},
            ).fetchall()

    # Index credits by (touchpoint_id, model).
    credits_by_tp: dict[int, dict[str, dict]] = {}
    for r in credits_rows:
        cid = int(r[0]); tid = int(r[1]); model = r[2]
        credits_by_tp.setdefault(tid, {})[model] = {
            "conversion_id": cid,
            "credit": float(r[3] or 0),
            "credited_revenue": float(r[4] or 0),
        }

    return {
        "customer_id": customer_id,
        "touchpoints": [
            {
                "id": int(r[0]),
                "channel": r[1],
                "campaign_id": int(r[2]) if r[2] is not None else None,
                "event_type": r[3],
                "event_at": _to_iso(r[4]),
                "metadata": r[5] or {},
                "credits": credits_by_tp.get(int(r[0]), {}),
            }
            for r in tps
        ],
        "conversions": [
            {
                "id": int(r[0]),
                "transaction_id": r[1],
                "revenue": float(r[2]) if r[2] is not None else None,
                "converted_at": _to_iso(r[3]),
                "metadata": r[4] or {},
            }
            for r in convs
        ],
    }
