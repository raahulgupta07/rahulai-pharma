"""
Ops Optimizer API — wraps dash.tools.ops_tools for the OpsPanel UI.

All endpoints project-scoped via project_slug. Auth via get_current_user.
Mirrors app/venture_api.py structure.
"""
from __future__ import annotations

import logging
import os
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import text

from app.auth import get_current_user
from dash.tools import ops_tools as ot

router = APIRouter(prefix="/api/ops", tags=["ops"])
log = logging.getLogger(__name__)


# ── Engines ──────────────────────────────────────────────────────────────

def _sql_engine():
    from db.session import get_sql_engine
    return get_sql_engine()


def _write_engine():
    try:
        from db.session import get_write_engine
        return get_write_engine()
    except Exception:
        return _sql_engine()


# ── Pydantic models ──────────────────────────────────────────────────────

class RegisterPortcoReq(BaseModel):
    deal_id: str
    legal_name: str
    investment_date: str
    ownership_pct: float
    board_seat: bool = False
    sector: Optional[str] = None
    stage_at_invest: Optional[str] = None
    fiscal_year_end: str = "DEC"


class IngestKpisReq(BaseModel):
    period: str
    metrics: List[dict]
    source: str = "manual"


class DetectReq(BaseModel):
    z_threshold: float = 2.0


class InitiativeCreateReq(BaseModel):
    # If auto=True, use propose_value_play; else manual create.
    auto: bool = False
    focus: Optional[str] = None
    # Manual fields:
    title: Optional[str] = None
    description: Optional[str] = None
    play_type: Optional[str] = None
    owner: Optional[str] = None
    target_metric: Optional[str] = None
    target_delta_pct: Optional[float] = None
    target_value_usd: Optional[float] = None
    status: str = "proposed"
    start_date: Optional[str] = None
    due_date: Optional[str] = None


class InitiativeUpdateReq(BaseModel):
    status: Optional[str] = None
    notes: Optional[str] = None
    owner: Optional[str] = None
    due_date: Optional[str] = None


class BoardPackReq(BaseModel):
    meeting_date: str


class WatchlistReq(BaseModel):
    reason: str


# ── Helpers ──────────────────────────────────────────────────────────────

def _ensure_portco(slug: str, portco_id: str) -> dict:
    with _sql_engine().connect() as cx:
        row = cx.execute(text("""
            SELECT id, project_slug, legal_name, investment_date,
                   ownership_pct, board_seat, sector, status,
                   stage_at_invest, fiscal_year_end, created_at, updated_at
            FROM dash.dash_portco
            WHERE id = :pc AND project_slug = :p
        """), {"pc": portco_id, "p": slug}).fetchone()
    if not row:
        raise HTTPException(404, "portco not found")
    return {
        "id": str(row[0]), "project_slug": row[1], "legal_name": row[2],
        "investment_date": row[3].isoformat() if row[3] else None,
        "ownership_pct": float(row[4]) if row[4] else None,
        "board_seat": row[5], "sector": row[6], "status": row[7],
        "stage_at_invest": row[8], "fiscal_year_end": row[9],
        "created_at": row[10].isoformat() if row[10] else None,
        "updated_at": row[11].isoformat() if row[11] else None,
    }


# ── Portfolio ────────────────────────────────────────────────────────────

@router.get("/{slug}/portcos")
async def list_portcos(slug: str, user=Depends(get_current_user)):
    with _sql_engine().connect() as cx:
        rows = cx.execute(text("""
            SELECT id, legal_name, status, investment_date, ownership_pct,
                   sector, board_seat, stage_at_invest, deal_id, updated_at
            FROM dash.dash_portco
            WHERE project_slug = :p
            ORDER BY investment_date DESC, legal_name
        """), {"p": slug}).fetchall()
    return {
        "ok": True,
        "portcos": [
            {
                "id": str(r[0]), "legal_name": r[1], "status": r[2],
                "investment_date": r[3].isoformat() if r[3] else None,
                "ownership_pct": float(r[4]) if r[4] else None,
                "sector": r[5], "board_seat": r[6],
                "stage_at_invest": r[7],
                "deal_id": str(r[8]) if r[8] else None,
                "updated_at": r[9].isoformat() if r[9] else None,
            } for r in rows
        ],
    }


@router.post("/{slug}/portcos")
async def create_portco(slug: str, req: RegisterPortcoReq,
                         user=Depends(get_current_user)):
    r = ot.register_portco(
        slug, req.deal_id, req.legal_name, req.investment_date,
        req.ownership_pct, req.board_seat, req.sector,
        req.stage_at_invest, req.fiscal_year_end,
    )
    if not r.get("ok"):
        raise HTTPException(400, r.get("error", "register failed"))
    return r


@router.get("/{slug}/portcos/{portco_id}")
async def get_portco(slug: str, portco_id: str,
                       user=Depends(get_current_user)):
    return {"ok": True, "portco": _ensure_portco(slug, portco_id)}


# ── KPIs ─────────────────────────────────────────────────────────────────

@router.get("/{slug}/portcos/{portco_id}/kpis")
async def list_kpis(slug: str, portco_id: str, since_periods: int = 12,
                     user=Depends(get_current_user)):
    _ensure_portco(slug, portco_id)
    r = ot.kpi_dashboard(portco_id, since_periods)
    if not r.get("ok"):
        raise HTTPException(500, r.get("error", "kpis failed"))
    return r


@router.post("/{slug}/portcos/{portco_id}/kpis")
async def ingest_kpis_endpoint(slug: str, portco_id: str,
                                 req: IngestKpisReq,
                                 user=Depends(get_current_user)):
    _ensure_portco(slug, portco_id)
    r = ot.ingest_kpis(portco_id, req.period, req.metrics, req.source)
    if not r.get("ok"):
        raise HTTPException(400, r.get("error", "ingest failed"))
    return r


# ── Anomalies ────────────────────────────────────────────────────────────

@router.get("/{slug}/portcos/{portco_id}/anomalies")
async def list_anomalies(slug: str, portco_id: str,
                           unacked_only: bool = False,
                           user=Depends(get_current_user)):
    _ensure_portco(slug, portco_id)
    q = """
        SELECT id, metric_name, period, severity, z_score, explanation,
               acknowledged, detected_at
        FROM dash.dash_portco_anomalies
        WHERE portco_id = :pc
    """
    if unacked_only:
        q += " AND acknowledged = false"
    q += " ORDER BY detected_at DESC LIMIT 200"
    with _sql_engine().connect() as cx:
        rows = cx.execute(text(q), {"pc": portco_id}).fetchall()
    return {
        "ok": True,
        "anomalies": [
            {
                "id": str(r[0]), "metric_name": r[1], "period": r[2],
                "severity": r[3],
                "z_score": float(r[4]) if r[4] is not None else None,
                "explanation": r[5], "acknowledged": r[6],
                "detected_at": r[7].isoformat() if r[7] else None,
            } for r in rows
        ],
    }


@router.post("/{slug}/portcos/{portco_id}/anomalies/detect")
async def detect_anomalies_endpoint(slug: str, portco_id: str,
                                      req: DetectReq = DetectReq(),
                                      user=Depends(get_current_user)):
    _ensure_portco(slug, portco_id)
    r = ot.detect_anomalies(portco_id, req.z_threshold)
    if not r.get("ok"):
        raise HTTPException(500, r.get("error", "detect failed"))
    return r


@router.post("/{slug}/portcos/{portco_id}/anomalies/{aid}/ack")
async def ack_anomaly(slug: str, portco_id: str, aid: str,
                       user=Depends(get_current_user)):
    _ensure_portco(slug, portco_id)
    try:
        with _write_engine().begin() as cx:
            r = cx.execute(text("""
                UPDATE dash.dash_portco_anomalies
                SET acknowledged = true
                WHERE id = :aid AND portco_id = :pc
                RETURNING id
            """), {"aid": aid, "pc": portco_id}).fetchone()
        if not r:
            raise HTTPException(404, "anomaly not found")
        return {"ok": True, "id": str(r[0])}
    except HTTPException:
        raise
    except Exception as e:
        log.exception("ack_anomaly failed")
        raise HTTPException(500, str(e))


# ── Initiatives ──────────────────────────────────────────────────────────

@router.get("/{slug}/portcos/{portco_id}/initiatives")
async def list_initiatives(slug: str, portco_id: str,
                             status: Optional[str] = None,
                             user=Depends(get_current_user)):
    _ensure_portco(slug, portco_id)
    q = """
        SELECT id, title, description, play_type, owner, target_metric,
               target_delta_pct, target_value_usd, status,
               start_date, due_date, created_at, updated_at
        FROM dash.dash_portco_initiatives
        WHERE portco_id = :pc
    """
    params: dict[str, Any] = {"pc": portco_id}
    if status:
        q += " AND status = :st"
        params["st"] = status
    q += " ORDER BY status, updated_at DESC LIMIT 200"
    with _sql_engine().connect() as cx:
        rows = cx.execute(text(q), params).fetchall()
    return {
        "ok": True,
        "initiatives": [
            {
                "id": str(r[0]), "title": r[1], "description": r[2],
                "play_type": r[3], "owner": r[4], "target_metric": r[5],
                "target_delta_pct": float(r[6]) if r[6] is not None else None,
                "target_value_usd": float(r[7]) if r[7] is not None else None,
                "status": r[8],
                "start_date": r[9].isoformat() if r[9] else None,
                "due_date": r[10].isoformat() if r[10] else None,
                "created_at": r[11].isoformat() if r[11] else None,
                "updated_at": r[12].isoformat() if r[12] else None,
            } for r in rows
        ],
    }


@router.post("/{slug}/portcos/{portco_id}/initiatives")
async def create_initiative(slug: str, portco_id: str,
                              req: InitiativeCreateReq,
                              user=Depends(get_current_user)):
    _ensure_portco(slug, portco_id)
    if req.auto:
        r = ot.propose_value_play(portco_id, req.focus, req.play_type,
                                    req.owner)
        if not r.get("ok"):
            raise HTTPException(400, r.get("error", "propose failed"))
        return r
    # Manual create.
    if not req.title:
        raise HTTPException(400, "title required for manual initiative")
    try:
        with _write_engine().begin() as cx:
            row = cx.execute(text("""
                INSERT INTO dash.dash_portco_initiatives
                    (portco_id, title, description, play_type, owner,
                     target_metric, target_delta_pct, target_value_usd,
                     status, start_date, due_date)
                VALUES (:pc, :t, :d, :pt, :o, :tm, :td, :tv, :st, :sd, :dd)
                RETURNING id
            """), {
                "pc": portco_id, "t": req.title, "d": req.description,
                "pt": req.play_type, "o": req.owner,
                "tm": req.target_metric, "td": req.target_delta_pct,
                "tv": req.target_value_usd, "st": req.status,
                "sd": req.start_date, "dd": req.due_date,
            }).fetchone()
        return {"ok": True, "initiative_id": str(row[0]),
                "status": req.status, "source": "manual"}
    except Exception as e:
        log.exception("create_initiative failed")
        raise HTTPException(500, str(e))


@router.patch("/{slug}/initiatives/{iid}")
async def patch_initiative(slug: str, iid: str,
                             req: InitiativeUpdateReq,
                             user=Depends(get_current_user)):
    # Verify initiative belongs to slug.
    with _sql_engine().connect() as cx:
        row = cx.execute(text("""
            SELECT i.id FROM dash.dash_portco_initiatives i
            JOIN dash.dash_portco p ON p.id = i.portco_id
            WHERE i.id = :iid AND p.project_slug = :s
        """), {"iid": iid, "s": slug}).fetchone()
    if not row:
        raise HTTPException(404, "initiative not found")
    r = ot.update_initiative(iid, req.status, req.notes, req.owner,
                              req.due_date)
    if not r.get("ok"):
        raise HTTPException(400, r.get("error", "update failed"))
    return r


# ── Health ───────────────────────────────────────────────────────────────

@router.get("/{slug}/health")
async def health(slug: str, user=Depends(get_current_user)):
    r = ot.portfolio_health(slug)
    if not r.get("ok"):
        raise HTTPException(500, r.get("error", "health failed"))
    return r


# ── Board pack ──────────────────────────────────────────────────────────

@router.post("/{slug}/portcos/{portco_id}/board-pack")
async def make_board_pack(slug: str, portco_id: str, req: BoardPackReq,
                            user=Depends(get_current_user)):
    _ensure_portco(slug, portco_id)
    r = ot.generate_board_pack(portco_id, req.meeting_date)
    if not r.get("ok"):
        raise HTTPException(500, r.get("error", "board pack failed"))
    return r


@router.get("/{slug}/portcos/{portco_id}/board-pack/{bp_id}.pdf")
async def stream_board_pack(slug: str, portco_id: str, bp_id: str,
                              user=Depends(get_current_user)):
    _ensure_portco(slug, portco_id)
    with _sql_engine().connect() as cx:
        row = cx.execute(text("""
            SELECT file_path, meeting_date FROM dash.dash_portco_board_packs
            WHERE id = :id AND portco_id = :pc
        """), {"id": bp_id, "pc": portco_id}).fetchone()
    if not row:
        raise HTTPException(404, "board pack not found")
    fp = row[0]
    if not fp or not os.path.exists(fp):
        raise HTTPException(410, "board pack file missing on disk")
    try:
        with open(fp, "rb") as f:
            data = f.read()
    except Exception as e:
        log.exception("read board pack failed")
        raise HTTPException(500, str(e))
    fname = f"board_pack_{row[1]}.pdf"
    return Response(
        content=data,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{fname}"'},
    )


# ── Watchlist ────────────────────────────────────────────────────────────

@router.post("/{slug}/portcos/{portco_id}/watchlist")
async def add_watchlist(slug: str, portco_id: str, req: WatchlistReq,
                          user=Depends(get_current_user)):
    _ensure_portco(slug, portco_id)
    r = ot.watchlist_add(portco_id, req.reason)
    if not r.get("ok"):
        raise HTTPException(400, r.get("error", "watchlist failed"))
    return r


# ── Benchmark ────────────────────────────────────────────────────────────

@router.get("/{slug}/portcos/{portco_id}/benchmark/{segment}")
async def benchmark(slug: str, portco_id: str, segment: str,
                     user=Depends(get_current_user)):
    _ensure_portco(slug, portco_id)
    r = ot.benchmark_portco(portco_id, segment)
    if not r.get("ok"):
        raise HTTPException(500, r.get("error", "benchmark failed"))
    return r
