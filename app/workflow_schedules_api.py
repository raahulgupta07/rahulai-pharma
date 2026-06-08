"""CRUD for dash.dash_workflow_schedules + run-now.

Endpoints under /api/agent-os/workflows/schedules:
  POST   /                       create schedule
  GET    /?workflow_id=N         list schedules for a workflow
  PATCH  /{schedule_id}          update cron/status (recomputes next_run_at)
  DELETE /{schedule_id}          delete schedule
  POST   /{schedule_id}/run-now  fire immediately (bypasses cron schedule)

Auth: reuses Request.state.user. Permission: workflow ownership check via
JOIN dash.dash_autonomous_workflows → dash.dash_projects / dash_project_shares,
mirroring agent_os_workflows._check_workflow_permission.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/api/agent-os/workflows/schedules",
    tags=["AgentOS Workflow Schedules"],
)


def _engine():
    from db import db_url
    return create_engine(db_url, poolclass=NullPool)


def _get_user(request: Request) -> dict:
    user = getattr(getattr(request, "state", None), "user", None)
    if not user:
        raise HTTPException(401, "Not authenticated")
    return user


def _is_super(user: dict) -> bool:
    # admin tier OR super may manage schedules
    return bool(user.get("is_admin"))


def _validate_cron(expr: str) -> bool:
    if not expr or not isinstance(expr, str):
        return False
    try:
        from croniter import croniter
        return bool(croniter.is_valid(expr))
    except Exception:
        # 5-field regex fallback
        import re
        return bool(re.match(r"^\s*\S+\s+\S+\s+\S+\s+\S+\s+\S+\s*$", expr))


def _next_from(cron_expr: str) -> Optional[datetime]:
    try:
        from croniter import croniter
        return croniter(cron_expr, datetime.now(timezone.utc)).get_next(datetime)
    except Exception:
        return None


def _check_workflow_owned(wf_id: int, user: dict) -> dict:
    """Return {workflow_id, project_slug} if user owns workflow OR super_admin.
    Mirrors agent_os_workflows._check_workflow_permission (editor+ implicitly
    via owned-or-shared)."""
    if _is_super(user):
        # super-admin bypass — still load row for project_slug
        eng = _engine()
        try:
            with eng.connect() as cn:
                row = cn.execute(text(
                    "SELECT id, project_slug FROM dash.dash_autonomous_workflows WHERE id = :id"
                ), {"id": wf_id}).fetchone()
        finally:
            eng.dispose()
        if not row:
            raise HTTPException(404, "workflow not found")
        return {"workflow_id": row[0], "project_slug": row[1]}

    # try reuse existing permission helper for parity with run_now logic
    try:
        from app.agent_os_workflows import _check_workflow_permission
        return _check_workflow_permission(wf_id, user.get("user_id") or user.get("id"),
                                          required_role="editor")
    except HTTPException:
        raise
    except Exception:
        logger.exception("workflow_schedules: permission helper failed")
        raise HTTPException(403, "forbidden")


def _row_to_dict(row) -> dict:
    return {
        "id": row[0],
        "workflow_id": row[1],
        "project_slug": row[2],
        "cron": row[3],
        "status": row[4],
        "next_run_at": row[5].isoformat() if row[5] else None,
        "last_run_at": row[6].isoformat() if row[6] else None,
        "last_run_id": row[7],
        "owner_user_id": row[8],
        "created_at": row[9].isoformat() if row[9] else None,
    }


_SELECT_COLS = (
    "id, workflow_id, project_slug, cron, status, next_run_at, "
    "last_run_at, last_run_id, owner_user_id, created_at"
)


class CreateScheduleReq(BaseModel):
    workflow_id: int
    cron: str


class PatchScheduleReq(BaseModel):
    cron: Optional[str] = None
    status: Optional[str] = None


@router.post("/")
def create_schedule(body: CreateScheduleReq, request: Request):
    user = _get_user(request)
    if not _validate_cron(body.cron):
        raise HTTPException(400, "invalid cron expression")
    perm = _check_workflow_owned(body.workflow_id, user)
    nxt = _next_from(body.cron)
    eng = _engine()
    try:
        with eng.begin() as cn:
            row = cn.execute(text(
                f"""
                INSERT INTO dash.dash_workflow_schedules
                  (workflow_id, project_slug, cron, status, next_run_at, owner_user_id)
                VALUES
                  (:wid, :slug, :cron, 'active', :nxt, :owner)
                RETURNING {_SELECT_COLS}
                """
            ), {
                "wid": body.workflow_id,
                "slug": perm.get("project_slug"),
                "cron": body.cron,
                "nxt": nxt,
                "owner": user.get("user_id") or user.get("id"),
            }).fetchone()
    finally:
        eng.dispose()
    return _row_to_dict(row)


@router.get("/")
def list_schedules(request: Request, workflow_id: int = Query(...)):
    user = _get_user(request)
    _check_workflow_owned(workflow_id, user)
    eng = _engine()
    try:
        with eng.connect() as cn:
            rows = cn.execute(text(
                f"SELECT {_SELECT_COLS} FROM dash.dash_workflow_schedules "
                "WHERE workflow_id = :wid ORDER BY id DESC"
            ), {"wid": workflow_id}).fetchall()
    finally:
        eng.dispose()
    return {"schedules": [_row_to_dict(r) for r in rows]}


def _load_schedule(sched_id: int) -> dict:
    eng = _engine()
    try:
        with eng.connect() as cn:
            row = cn.execute(text(
                f"SELECT {_SELECT_COLS} FROM dash.dash_workflow_schedules WHERE id = :id"
            ), {"id": sched_id}).fetchone()
    finally:
        eng.dispose()
    if not row:
        raise HTTPException(404, "schedule not found")
    return _row_to_dict(row)


@router.patch("/{schedule_id}")
def patch_schedule(schedule_id: int, body: PatchScheduleReq, request: Request):
    user = _get_user(request)
    sched = _load_schedule(schedule_id)
    _check_workflow_owned(sched["workflow_id"], user)

    sets = []
    params: dict = {"id": schedule_id}
    if body.cron is not None:
        if not _validate_cron(body.cron):
            raise HTTPException(400, "invalid cron expression")
        sets.append("cron = :cron")
        params["cron"] = body.cron
        nxt = _next_from(body.cron)
        sets.append("next_run_at = :nxt")
        params["nxt"] = nxt
    if body.status is not None:
        if body.status not in ("active", "paused"):
            raise HTTPException(400, "status must be active|paused")
        sets.append("status = :st")
        params["st"] = body.status
    if not sets:
        return sched

    eng = _engine()
    try:
        with eng.begin() as cn:
            row = cn.execute(text(
                f"UPDATE dash.dash_workflow_schedules SET {', '.join(sets)} "
                f"WHERE id = :id RETURNING {_SELECT_COLS}"
            ), params).fetchone()
    finally:
        eng.dispose()
    return _row_to_dict(row)


@router.delete("/{schedule_id}")
def delete_schedule(schedule_id: int, request: Request):
    user = _get_user(request)
    sched = _load_schedule(schedule_id)
    _check_workflow_owned(sched["workflow_id"], user)
    eng = _engine()
    try:
        with eng.begin() as cn:
            cn.execute(text(
                "DELETE FROM dash.dash_workflow_schedules WHERE id = :id"
            ), {"id": schedule_id})
    finally:
        eng.dispose()
    return {"ok": True, "deleted_id": schedule_id}


@router.post("/{schedule_id}/run-now")
def run_now(schedule_id: int, request: Request):
    user = _get_user(request)
    sched = _load_schedule(schedule_id)
    perm = _check_workflow_owned(sched["workflow_id"], user)

    try:
        from dash.cron.workflow_scheduler import _fire_workflow
    except Exception:
        logger.exception("workflow_schedules: fire helper import failed")
        raise HTTPException(500, "scheduler unavailable")

    run_id = _fire_workflow(sched["workflow_id"], perm.get("project_slug"))
    if not run_id:
        return {"ok": False, "error": "failed to start"}
    return {"ok": True, "run_id": run_id, "schedule_id": schedule_id}


__all__ = ["router"]
