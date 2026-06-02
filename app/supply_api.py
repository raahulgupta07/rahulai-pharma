"""
Supply Sentry API — wraps dash.tools.supply_tools for the SupplyPanel UI.

All endpoints project/tenant-scoped via slug path param. Auth via
get_current_user. Mirrors app/ops_api.py + app/market_api.py shape.
"""
from __future__ import annotations

import logging
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from app.auth import get_current_user
from dash.tools import supply_tools as st

router = APIRouter(prefix="/api/supply", tags=["supply"])
log = logging.getLogger(__name__)


def _sql_engine():
    from db.session import get_sql_engine
    return get_sql_engine()


def _write_engine():
    try:
        from db.session import get_write_engine
        return get_write_engine()
    except Exception:
        return _sql_engine()


# ── Pydantic ─────────────────────────────────────────────────────────────

class RegisterSupplierReq(BaseModel):
    legal_name: str
    country: str
    tier: str
    region: Optional[str] = None
    financial_health_score: Optional[float] = None
    metadata: Optional[dict] = None


class LinkSkuReq(BaseModel):
    tenant_slug: str
    sku: str
    lead_time_days: int
    unit_cost_usd: float
    mou_units: Optional[float] = None
    category: Optional[str] = None
    sku_description: Optional[str] = None
    is_primary: bool = True


class EventReq(BaseModel):
    event_type: str
    severity: str = "info"
    title: str
    body: Optional[str] = None
    source_url: Optional[str] = None
    payload: Optional[dict] = None


class ConsentReq(BaseModel):
    share_aggregate: bool = False
    share_supplier_list: bool = False


# ── Suppliers ────────────────────────────────────────────────────────────

@router.post("/{slug}/suppliers")
async def register_supplier_endpoint(slug: str, req: RegisterSupplierReq,
                                       user=Depends(get_current_user)):
    r = st.register_supplier(req.legal_name, req.country, req.tier,
                              req.region, req.financial_health_score,
                              req.metadata)
    if not r.get("ok"):
        raise HTTPException(400, r.get("error", "register failed"))
    return r


@router.post("/{slug}/suppliers/{supplier_id}/skus")
async def link_sku_endpoint(slug: str, supplier_id: str, req: LinkSkuReq,
                             user=Depends(get_current_user)):
    r = st.link_sku(supplier_id, req.tenant_slug, req.sku,
                     req.lead_time_days, req.unit_cost_usd,
                     req.mou_units, req.category, req.sku_description,
                     req.is_primary)
    if not r.get("ok"):
        raise HTTPException(400, r.get("error", "link failed"))
    return r


@router.post("/{slug}/suppliers/{supplier_id}/events")
async def ingest_event_endpoint(slug: str, supplier_id: str, req: EventReq,
                                  user=Depends(get_current_user)):
    r = st.ingest_supplier_event(supplier_id, req.event_type, req.severity,
                                   req.title, req.body, req.source_url,
                                   req.payload)
    if not r.get("ok"):
        raise HTTPException(400, r.get("error", "event failed"))
    return r


@router.post("/{slug}/suppliers/{supplier_id}/score")
async def score_endpoint(slug: str, supplier_id: str,
                          user=Depends(get_current_user)):
    r = st.score_supplier(supplier_id)
    if not r.get("ok"):
        raise HTTPException(500, r.get("error", "score failed"))
    return r


@router.get("/{slug}/suppliers/{supplier_id}/exposure")
async def exposure_endpoint(slug: str, supplier_id: str,
                              user=Depends(get_current_user)):
    # `slug` is the requesting tenant
    r = st.cross_tenant_exposure(supplier_id, slug)
    if not r.get("ok"):
        raise HTTPException(500, r.get("error", "exposure failed"))
    return r


# ── Anomalies / scorecards / report ──────────────────────────────────────

@router.get("/{slug}/anomalies")
async def anomalies_endpoint(slug: str, z: float = 2.0,
                              supplier_id: Optional[str] = None,
                              user=Depends(get_current_user)):
    out = st.detect_supply_anomaly(supplier_id or None, z)
    return {"ok": True, "anomalies": out}


@router.get("/{slug}/scorecard")
async def scorecard_endpoint(slug: str, user=Depends(get_current_user)):
    r = st.resilience_scorecard(slug)
    if not r.get("ok"):
        raise HTTPException(500, r.get("error", "scorecard failed"))
    return r


@router.get("/{slug}/alt-suppliers")
async def alt_suppliers_endpoint(slug: str, sku: str,
                                   exclude_supplier_id: Optional[str] = None,
                                   user=Depends(get_current_user)):
    r = st.propose_alt_supplier(sku, slug, exclude_supplier_id)
    if not r.get("ok"):
        raise HTTPException(400, r.get("error", "alt-supplier failed"))
    return r


@router.get("/{slug}/report")
async def report_endpoint(slug: str, days: int = 7,
                           user=Depends(get_current_user)):
    r = st.generate_supply_risk_report(slug, days)
    if not r.get("ok"):
        raise HTTPException(500, r.get("error", "report failed"))
    return r


@router.post("/{slug}/news-scan")
async def news_scan_endpoint(slug: str, country: Optional[str] = None,
                              since_hours: int = 24,
                              user=Depends(get_current_user)):
    out = st.news_scan_suppliers(country, since_hours)
    return {"ok": True, "events": out}


# ── Consent ──────────────────────────────────────────────────────────────

@router.get("/{slug}/consent")
async def get_consent(slug: str, user=Depends(get_current_user)):
    try:
        with _sql_engine().connect() as cx:
            row = cx.execute(text("""
                SELECT tenant_slug, share_aggregate, share_supplier_list
                FROM dash.dash_supply_consent
                WHERE tenant_slug = :s
            """), {"s": slug}).fetchone()
        if not row:
            return {"ok": True, "tenant_slug": slug,
                    "share_aggregate": False,
                    "share_supplier_list": False,
                    "exists": False}
        return {"ok": True, "tenant_slug": row[0],
                "share_aggregate": bool(row[1]),
                "share_supplier_list": bool(row[2]),
                "exists": True}
    except Exception as e:
        log.exception("get_consent failed")
        raise HTTPException(500, str(e))


@router.put("/{slug}/consent")
async def put_consent(slug: str, req: ConsentReq,
                       user=Depends(get_current_user)):
    try:
        with _write_engine().begin() as cx:
            cx.execute(text("""
                INSERT INTO dash.dash_supply_consent
                    (tenant_slug, share_aggregate, share_supplier_list)
                VALUES (:s, :a, :l)
                ON CONFLICT (tenant_slug) DO UPDATE SET
                    share_aggregate = EXCLUDED.share_aggregate,
                    share_supplier_list = EXCLUDED.share_supplier_list
            """), {"s": slug, "a": req.share_aggregate,
                   "l": req.share_supplier_list})
        return {"ok": True, "tenant_slug": slug,
                "share_aggregate": req.share_aggregate,
                "share_supplier_list": req.share_supplier_list}
    except Exception as e:
        log.exception("put_consent failed")
        raise HTTPException(500, str(e))
