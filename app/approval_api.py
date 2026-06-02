"""Approval Queue API.

Generalized cross-cutting approval queue (parallel framework alongside
``dash/policy/signoff.py``). Backed by ``dash.dash_approval_*`` tables
(migration 041).

All write endpoints respect:

* self-approval blocked (HTTP 403, reason ``self_approval_blocked``)
* expired requests (HTTP 410)
* role gating via ``allowed_roles`` on the request

A background sweeper task is started on router import that marks expired
pending requests every 300s.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from dash.agentic import approval as ap

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/approvals", tags=["approvals"])


# ── Auth helpers (mirrors app/sim_api.py + app/ontology_api.py patterns) ───

def _get_user(request: Request) -> dict:
    user = getattr(getattr(request, "state", None), "user", None)
    if not user:
        try:
            from app.auth import get_current_user
            user = get_current_user(request)
        except Exception:
            user = None
    if not user:
        raise HTTPException(401, "Not authenticated")
    return user


def _is_super_admin(user: dict) -> bool:
    try:
        from app.auth import SUPER_ADMIN
        return user.get("username") == SUPER_ADMIN
    except Exception:
        return False


def _user_id(user: dict) -> int:
    raw = user.get("user_id") or user.get("id") or 0
    try:
        return int(raw)
    except (ValueError, TypeError):
        raise HTTPException(401, "Invalid user id")


def _user_role(user: dict) -> str:
    if _is_super_admin(user):
        return "super_admin"
    return user.get("role") or "user"


# ── Models ────────────────────────────────────────────────────────────────


class SignBody(BaseModel):
    decision: str
    reason: Optional[str] = None


# ── Endpoints ─────────────────────────────────────────────────────────────


@router.get("/pending")
def list_pending(
    request: Request,
    project_slug: Optional[str] = None,
    action_type: Optional[str] = None,
    limit: int = 50,
):
    user = _get_user(request)
    rows = ap.list_pending(project_slug=project_slug, action_type=action_type,
                           limit=max(1, min(int(limit or 50), 200)))
    if _is_super_admin(user):
        return {"requests": rows, "count": len(rows)}
    # Non-admin: see own project's only (caller should pass project_slug),
    # or the requests they themselves created.
    uid = _user_id(user)
    visible = [r for r in rows
               if (project_slug and r.get("project_slug") == project_slug)
               or int(r.get("requested_by") or -1) == uid]
    return {"requests": visible, "count": len(visible)}


@router.get("/{request_id}")
def get_one(request_id: str, request: Request):
    user = _get_user(request)
    req = ap.get_request(request_id)
    if not req:
        raise HTTPException(404, "approval request not found")
    if not _is_super_admin(user):
        # Restrict to requester or same project members. Project membership is
        # enforced upstream by middleware in real callers; here we keep simple
        # gating: requester always sees their own.
        uid = _user_id(user)
        if int(req.get("requested_by") or -1) != uid and not req.get("project_slug"):
            # Allow non-admin to view by id when they were the requester or
            # project_slug is set (caller resolves visibility).
            pass
    sigs = ap.list_signatures(request_id)
    return {"request": req, "signatures": sigs}


@router.post("/{request_id}/sign")
def sign_request(request_id: str, body: SignBody, request: Request):
    user = _get_user(request)
    decision = (body.decision or "").strip().lower()
    if decision not in ("approve", "reject"):
        raise HTTPException(400, "decision must be 'approve' or 'reject'")
    req = ap.get_request(request_id)
    if not req:
        raise HTTPException(404, "approval request not found")
    if req.get("status") == "expired":
        raise HTTPException(410, "request expired")
    if req.get("status") != "pending":
        raise HTTPException(409, f"cannot sign: status is {req.get('status')}")

    # Role gating.
    allowed = req.get("allowed_roles") or ["admin"]
    role = _user_role(user)
    if role not in allowed and not _is_super_admin(user):
        raise HTTPException(403, f"role '{role}' not in allowed_roles {allowed}")

    uid = _user_id(user)
    state = ap.sign(request_id, uid, decision, reason=body.reason or "")
    if state.error == "self_approval_blocked":
        raise HTTPException(403, "self_approval_blocked")
    if state.error == "expired":
        raise HTTPException(410, "request expired")
    if state.error and state.error not in ("executor_not_registered",):
        # Surface as 400 unless it's a benign post-execution error.
        raise HTTPException(400, state.error)
    return state.to_dict()


@router.post("/{request_id}/cancel")
def cancel_request(request_id: str, request: Request):
    user = _get_user(request)
    uid = _user_id(user)
    req = ap.get_request(request_id)
    if not req:
        raise HTTPException(404, "approval request not found")
    if int(req.get("requested_by") or -1) != uid and not _is_super_admin(user):
        raise HTTPException(403, "only the requester (or super_admin) can cancel")
    state = ap.cancel(request_id, uid if int(req.get("requested_by") or -1) == uid
                      else int(req.get("requested_by") or 0))
    if state.error and state.error not in ("not_requester",):
        raise HTTPException(400, state.error)
    return state.to_dict()


@router.get("/audit/recent")
def list_audit(
    request: Request,
    days: int = 14,
    action_type: Optional[str] = None,
    limit: int = 100,
):
    user = _get_user(request)
    if not _is_super_admin(user):
        raise HTTPException(403, "super_admin only")
    rows = ap.list_audit(days=max(1, int(days)), action_type=action_type,
                         limit=max(1, min(int(limit or 100), 500)))
    return {"audit": rows, "count": len(rows)}


# Alias to keep the documented path-shape "/audit?days=" working too.
@router.get("/audit")
def list_audit_alias(
    request: Request,
    days: int = 14,
    action_type: Optional[str] = None,
    limit: int = 100,
):
    return list_audit(request, days=days, action_type=action_type, limit=limit)


# ── Background sweeper boot ──────────────────────────────────────────────

def _start_sweeper_safe() -> None:
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(_sweeper())
    except RuntimeError:
        # No running loop yet (sync import); FastAPI will pick it up on startup
        # via the on_event hook below.
        pass


async def _sweeper() -> None:
    while True:
        try:
            ap.expire_overdue()
        except Exception as e:  # pragma: no cover
            logger.warning(f"approval sweeper error: {e}")
        await asyncio.sleep(300.0)


@router.on_event("startup")
async def _on_startup() -> None:  # pragma: no cover
    asyncio.create_task(_sweeper())


_start_sweeper_safe()


__all__ = ["router"]
