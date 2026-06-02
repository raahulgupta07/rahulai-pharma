"""
Venture API — thin wrapper over dash.tools.venture_tools for the VenturePanel UI.

Endpoints scoped per project_slug. Reuses standard auth dependency.
"""
from __future__ import annotations

import logging
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from app.auth import get_current_user
from dash.tools import venture_tools as vt
from dash.tools import venture_memo as vm
from dash.learning import venture_reward as vr

router = APIRouter(prefix="/api/venture", tags=["venture"])
log = logging.getLogger(__name__)


class CashflowReq(BaseModel):
    cashflows: List[float]
    wacc: float = 0.12
    terminal_growth: float = 0.03


class SensitivityReq(BaseModel):
    base_cashflows: List[float]
    wacc_range: List[float]
    growth_range: List[float]


class DealReq(BaseModel):
    name: str
    stage: str = "screening"
    sector: str = ""
    geography: str = ""
    ask_amount: float = 0.0


class ScenarioReq(BaseModel):
    deal_id: str
    name: str
    inputs: dict = {}
    results: dict = {}
    verdict: str = "hold"


_ALLOWED_STAGES = {"screening", "due_diligence", "term_sheet", "closed", "passed",
                   "diligence", "ic", "shortlist", "pass"}  # incl. legacy values


class DealStatusReq(BaseModel):
    stage: str
    reason: Optional[str] = None


@router.post("/{slug}/deals/{deal_id}/status")
async def update_deal_status(slug: str, deal_id: str, req: DealStatusReq,
                             user=Depends(get_current_user)):
    """Update deal stage. On transition to 'closed' or 'passed', emit a reward
    signal closing the self-learning loop on the Deal Analyst's verdict.

    Returns: {ok, deal_id, stage, reward?: {signal, reason, scenario_verdict}}
    """
    stage = (req.stage or "").strip().lower()
    if stage not in _ALLOWED_STAGES:
        raise HTTPException(400, f"invalid stage '{stage}'; allowed: "
                            f"{sorted(_ALLOWED_STAGES)}")

    # Map task-spec stages to existing status values (backward compat).
    status_value = "pass" if stage == "passed" else stage

    try:
        from db.session import get_write_engine
        from sqlalchemy import text as _t
        eng = get_write_engine()
        with eng.begin() as cn:
            row = cn.execute(_t(
                "SELECT id, project_slug, status FROM dash.dash_venture_deals "
                " WHERE id = CAST(:d AS uuid) AND project_slug = :p"
            ), {"d": deal_id, "p": slug}).fetchone()
            if not row:
                raise HTTPException(404, "deal not found in this project")
            prior_status = (row[2] or "").lower()

            cn.execute(_t(
                "UPDATE dash.dash_venture_deals "
                "   SET stage = :st, status = :stt, updated_at = now() "
                " WHERE id = CAST(:d AS uuid) AND project_slug = :p"
            ), {"st": stage, "stt": status_value, "d": deal_id, "p": slug})
    except HTTPException:
        raise
    except Exception as e:
        log.exception("update_deal_status failed deal=%s slug=%s", deal_id, slug)
        raise HTTPException(500, f"db update failed: {e}")

    out: dict = {"ok": True, "deal_id": deal_id, "stage": stage,
                 "prior_stage": prior_status}

    # Reward signal on terminal transitions, idempotent-friendly:
    # fire only when the stage actually changes into a terminal state.
    if status_value in ("closed", "pass") and status_value != prior_status:
        try:
            rr = vr.reward_for_deal(deal_id)
            if rr.get("ok"):
                out["reward"] = {
                    "signal": rr.get("reward"),
                    "reason": rr.get("reason"),
                    "scenario_verdict": rr.get("scenario_verdict"),
                    "deal_status": rr.get("deal_status"),
                    "agent": "deal_analyst",
                }
            else:
                out["reward_error"] = rr.get("error", "reward_compute_failed")
        except Exception as e:
            log.exception("reward emit failed deal=%s", deal_id)
            out["reward_error"] = f"exception: {e}"

    return out


class UnitEconReq(BaseModel):
    cac: float
    ltv: float
    gross_margin: float = 1.0
    payback_months: Optional[float] = None


class PartnerFitReq(BaseModel):
    self_caps: List[str]
    partner_caps: List[str]


@router.get("/{slug}/deals")
async def list_deals(slug: str, status: Optional[str] = None, user=Depends(get_current_user)):
    r = vt.list_deals(slug, status)
    if not r.get("ok"):
        raise HTTPException(500, r.get("error", "list failed"))
    return r


@router.post("/{slug}/deals")
async def create_deal(slug: str, req: DealReq, user=Depends(get_current_user)):
    uid = user.get("id") if isinstance(user, dict) else getattr(user, "id", None)
    r = vt.save_deal(slug, req.name, req.stage, req.sector, req.geography, req.ask_amount, uid)
    if not r.get("ok"):
        raise HTTPException(500, r.get("error", "save failed"))
    return r


@router.post("/{slug}/dcf")
async def run_dcf(slug: str, req: CashflowReq, user=Depends(get_current_user)):
    r = vt.dcf(req.cashflows, req.wacc, req.terminal_growth)
    if not r.get("ok"):
        raise HTTPException(400, r.get("error", "dcf failed"))
    return r


@router.post("/{slug}/irr")
async def run_irr(slug: str, req: CashflowReq, user=Depends(get_current_user)):
    r = vt.irr_moic(req.cashflows)
    if not r.get("ok"):
        raise HTTPException(400, r.get("error", "irr failed"))
    return r


@router.post("/{slug}/sensitivity")
async def run_sensitivity(slug: str, req: SensitivityReq, user=Depends(get_current_user)):
    r = vt.sensitivity_grid(req.base_cashflows, req.wacc_range, req.growth_range)
    if not r.get("ok"):
        raise HTTPException(400, r.get("error", "sensitivity failed"))
    return r


@router.post("/{slug}/unit-economics")
async def run_unit_econ(slug: str, req: UnitEconReq, user=Depends(get_current_user)):
    r = vt.unit_economics(req.cac, req.ltv, req.gross_margin, req.payback_months)
    if not r.get("ok"):
        raise HTTPException(400, r.get("error", "unit-econ failed"))
    return r


@router.post("/{slug}/partner-fit")
async def run_partner_fit(slug: str, req: PartnerFitReq, user=Depends(get_current_user)):
    r = vt.partner_fit_score(req.self_caps, req.partner_caps)
    if not r.get("ok"):
        raise HTTPException(400, r.get("error", "partner-fit failed"))
    return r


@router.post("/{slug}/scenarios")
async def save_scenario(slug: str, req: ScenarioReq, user=Depends(get_current_user)):
    r = vt.save_scenario(req.deal_id, req.name, req.inputs, req.results, req.verdict)
    if not r.get("ok"):
        raise HTTPException(500, r.get("error", "save scenario failed"))
    return r


@router.get("/{slug}/deals/{deal_id}/memo.pdf")
async def memo_pdf(slug: str, deal_id: str, user=Depends(get_current_user)):
    try:
        pdf_bytes = vm.generate_pdf(deal_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    except Exception as e:
        log.exception("memo pdf failed")
        raise HTTPException(500, f"pdf failed: {e}")
    fname = f"ic_memo_{deal_id[:8]}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.get("/{slug}/deals/{deal_id}/memo.pptx")
async def memo_pptx(slug: str, deal_id: str, user=Depends(get_current_user)):
    try:
        pptx_bytes = vm.generate_pptx(deal_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    except Exception as e:
        log.exception("memo pptx failed")
        raise HTTPException(500, f"pptx failed: {e}")
    fname = f"ic_memo_{deal_id[:8]}.pptx"
    return Response(
        content=pptx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.get("/{slug}/deals/{deal_id}/memo.xlsx")
async def memo_xlsx(slug: str, deal_id: str, user=Depends(get_current_user)):
    try:
        from dash.tools import venture_excel as vx
        xlsx_bytes = vx.generate_xlsx(deal_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    except Exception as e:
        log.exception("memo xlsx failed")
        raise HTTPException(500, f"xlsx failed: {e}")
    fname = f"ic_memo_{deal_id[:8]}.xlsx"
    return Response(
        content=xlsx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )
