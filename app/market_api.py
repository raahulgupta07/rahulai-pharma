"""
Market Sentinel API — wraps dash.tools.market_tools for the MarketPanel UI.

All endpoints project-scoped via project_slug. Auth via get_current_user.
Mirrors app/ops_api.py structure.
"""
from __future__ import annotations

import logging
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from app.auth import get_current_user
from dash.tools import market_tools as mt

router = APIRouter(prefix="/api/market", tags=["market"])
log = logging.getLogger(__name__)


def _sql_engine():
    from db.session import get_sql_engine
    return get_sql_engine()


# ── Pydantic models ──────────────────────────────────────────────────────

class SignalIngestReq(BaseModel):
    signal_type: str
    source_url: Optional[str] = None
    title: str
    body: Optional[str] = ""
    sector: Optional[str] = None
    geography: Optional[str] = None
    published_at: Optional[str] = None


class TamSamReq(BaseModel):
    sector: str
    geography: str
    deal_id: Optional[str] = None
    methodology: str = "bottom_up"


# ── Endpoints ────────────────────────────────────────────────────────────

@router.post("/{slug}/signals")
async def ingest_signal(slug: str, req: SignalIngestReq,
                          user=Depends(get_current_user)):
    r = mt.ingest_market_signal(
        slug, req.signal_type, req.source_url or "", req.title,
        req.body or "", req.sector, req.geography, req.published_at,
    )
    if not r.get("ok"):
        raise HTTPException(400, r.get("error", "ingest failed"))
    return r


@router.get("/{slug}/signals/search")
async def signals_search(slug: str, q: str, top_k: int = 10,
                          sector: Optional[str] = None,
                          user=Depends(get_current_user)):
    r = mt.search_signals(slug, q, top_k, sector)
    if not r.get("ok"):
        raise HTTPException(500, r.get("error", "search failed"))
    return r


@router.get("/{slug}/signals")
async def signals_list(slug: str, signal_type: Optional[str] = None,
                        sector: Optional[str] = None,
                        limit: int = 50,
                        user=Depends(get_current_user)):
    limit = max(1, min(int(limit), 200))
    q = """
        SELECT id, sector, geography, signal_type, source_url, title,
               body, published_at, ingested_at
        FROM dash.dash_market_signals
        WHERE project_slug = :p
    """
    params: dict[str, Any] = {"p": slug}
    if signal_type:
        q += " AND signal_type = :st"
        params["st"] = signal_type
    if sector:
        q += " AND sector ILIKE :sec"
        params["sec"] = f"%{sector}%"
    q += " ORDER BY COALESCE(published_at, ingested_at) DESC LIMIT :lim"
    params["lim"] = limit
    with _sql_engine().connect() as cx:
        rows = cx.execute(text(q), params).fetchall()
    return {
        "ok": True,
        "signals": [
            {
                "id": str(r[0]), "sector": r[1], "geography": r[2],
                "signal_type": r[3], "source_url": r[4], "title": r[5],
                "body": (r[6] or "")[:500],
                "published_at": r[7].isoformat() if r[7] else None,
                "ingested_at": r[8].isoformat() if r[8] else None,
            } for r in rows
        ],
        "count": len(rows),
    }


@router.post("/{slug}/tam-sam")
async def compute_tam_sam(slug: str, req: TamSamReq,
                            user=Depends(get_current_user)):
    r = mt.estimate_tam_sam(slug, req.sector, req.geography,
                             req.deal_id, req.methodology)
    if not r.get("ok"):
        raise HTTPException(400, r.get("error", "estimate failed"))
    return r


@router.get("/{slug}/tam-sam")
async def list_tam_sam(slug: str, sector: Optional[str] = None,
                        deal_id: Optional[str] = None,
                        limit: int = 50,
                        user=Depends(get_current_user)):
    limit = max(1, min(int(limit), 200))
    q = """
        SELECT id, deal_id, sector, geography, tam_usd, sam_usd, som_usd,
               methodology, assumptions, computed_at, computed_by
        FROM dash.dash_tam_sam_estimates
        WHERE project_slug = :p
    """
    params: dict[str, Any] = {"p": slug}
    if sector:
        q += " AND sector ILIKE :sec"
        params["sec"] = f"%{sector}%"
    if deal_id:
        q += " AND deal_id = :d"
        params["d"] = deal_id
    q += " ORDER BY computed_at DESC LIMIT :lim"
    params["lim"] = limit
    with _sql_engine().connect() as cx:
        rows = cx.execute(text(q), params).fetchall()
    return {
        "ok": True,
        "estimates": [
            {
                "id": str(r[0]),
                "deal_id": str(r[1]) if r[1] else None,
                "sector": r[2], "geography": r[3],
                "tam_usd": float(r[4]) if r[4] is not None else None,
                "sam_usd": float(r[5]) if r[5] is not None else None,
                "som_usd": float(r[6]) if r[6] is not None else None,
                "methodology": r[7],
                "assumptions": r[8] if isinstance(r[8], dict) else {},
                "computed_at": r[9].isoformat() if r[9] else None,
                "computed_by": r[10],
            } for r in rows
        ],
        "count": len(rows),
    }


@router.get("/{slug}/competitors")
async def list_competitors(slug: str, sector: str,
                            geography: Optional[str] = None,
                            user=Depends(get_current_user)):
    r = mt.competitor_map(slug, sector, geography)
    if not r.get("ok"):
        raise HTTPException(500, r.get("error", "competitor_map failed"))
    return r


@router.get("/{slug}/trends")
async def get_trends(slug: str, sector: str = "", days: int = 90,
                      user=Depends(get_current_user)):
    r = mt.trend_detect(slug, sector, days)
    if not r.get("ok"):
        raise HTTPException(500, r.get("error", "trend_detect failed"))
    return r


@router.get("/{slug}/deals/{deal_id}/market-summary")
async def market_summary_for_deal(slug: str, deal_id: str,
                                    user=Depends(get_current_user)):
    # Verify deal belongs to project (cross-tenant guard).
    with _sql_engine().connect() as cx:
        row = cx.execute(text("""
            SELECT id FROM dash.dash_venture_deals
            WHERE id = :d AND project_slug = :p
        """), {"d": deal_id, "p": slug}).fetchone()
    if not row:
        raise HTTPException(404, "deal not found for this project")
    r = mt.summarize_market_for_memo(deal_id)
    return r
