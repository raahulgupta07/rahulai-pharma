"""Dash-OS Phase 2E — Agent schedules CRUD + run history."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/agent-schedules", tags=["agent-schedules"])


def _get_engine():
    try:
        from db.session import get_sql_engine
        return get_sql_engine()
    except Exception:
        try:
            from db import get_sql_engine  # type: ignore
            return get_sql_engine()
        except Exception:
            return None


def _user_dep():
    try:
        from app.auth import _get_user
        return _get_user
    except Exception:
        def _noop():
            return {"id": 0, "username": "anonymous", "is_super_admin": False}
        return _noop


_get_user = _user_dep()


class CreateSchedule(BaseModel):
    project_slug: Optional[str] = None
    name: str
    description: Optional[str] = None
    kind: str  # 'cron' | 'interval' | 'once'
    cron_expr: Optional[str] = None
    interval_seconds: Optional[int] = None
    at: Optional[str] = None  # ISO datetime for kind='once'
    prompt: str
    agent_target: str = "leader"
    max_runs: Optional[int] = None


class PatchSchedule(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    cron_expr: Optional[str] = None
    prompt: Optional[str] = None
    enabled: Optional[bool] = None


@router.get("")
def list_schedules(
    project_slug: Optional[str] = Query(None),
    enabled_only: bool = Query(True),
    limit: int = Query(50, ge=1, le=500),
    user=Depends(_get_user),
):
    from dash.tools.scheduler_tools import _list_schedules
    return _list_schedules(project_slug=project_slug, enabled_only=enabled_only, limit=limit)


@router.post("")
def create_schedule(body: CreateSchedule, user=Depends(_get_user)):
    from dash.tools.scheduler_tools import (
        _schedule_recurring, _schedule_interval, _schedule_once,
    )
    if body.kind == "cron":
        if not body.cron_expr:
            raise HTTPException(400, "cron_expr required")
        return _schedule_recurring(
            name=body.name, cron=body.cron_expr, prompt=body.prompt,
            agent_target=body.agent_target, max_runs=body.max_runs,
            description=body.description,
        )
    if body.kind == "interval":
        if not body.interval_seconds:
            raise HTTPException(400, "interval_seconds required")
        return _schedule_interval(
            name=body.name, every_seconds=body.interval_seconds, prompt=body.prompt,
            agent_target=body.agent_target, max_runs=body.max_runs,
            description=body.description,
        )
    if body.kind == "once":
        if not body.at:
            raise HTTPException(400, "at required")
        return _schedule_once(
            name=body.name, at=body.at, prompt=body.prompt,
            agent_target=body.agent_target, description=body.description,
        )
    raise HTTPException(400, f"invalid kind: {body.kind}")


@router.get("/{sid}")
def get_schedule(sid: str, user=Depends(_get_user)):
    eng = _get_engine()
    if eng is None:
        raise HTTPException(503, "db unavailable")
    from sqlalchemy import text
    with eng.connect() as conn:
        row = conn.execute(
            text("SELECT * FROM dash.dash_agent_schedules WHERE id=:id"),
            {"id": sid},
        ).mappings().first()
        if not row:
            raise HTTPException(404, "schedule_not_found")
        runs = conn.execute(
            text(
                "SELECT id, started_at, finished_at, status, response_excerpt, error "
                "FROM dash.dash_agent_schedule_runs WHERE schedule_id=:id "
                "ORDER BY started_at DESC LIMIT 10"
            ),
            {"id": sid},
        ).mappings().all()
    return {"schedule": dict(row), "recent_runs": [dict(r) for r in runs]}


@router.patch("/{sid}")
def patch_schedule(sid: str, body: PatchSchedule, user=Depends(_get_user)):
    eng = _get_engine()
    if eng is None:
        raise HTTPException(503, "db unavailable")
    fields = {k: v for k, v in body.dict().items() if v is not None}
    if not fields:
        return {"ok": True, "patched": 0}
    from sqlalchemy import text
    sets = ", ".join(f"{k}=:{k}" for k in fields)
    with eng.begin() as conn:
        r = conn.execute(
            text(f"UPDATE dash.dash_agent_schedules SET {sets}, updated_at=now() WHERE id=:id"),
            {**fields, "id": sid},
        )
    return {"ok": r.rowcount > 0, "patched": r.rowcount}


@router.delete("/{sid}")
def delete_schedule(sid: str, user=Depends(_get_user)):
    from dash.tools.scheduler_tools import _delete_schedule
    return _delete_schedule(sid)


@router.post("/{sid}/run-now")
def run_now(sid: str, user=Depends(_get_user)):
    from dash.tools.scheduler_tools import _run_schedule_now
    return _run_schedule_now(sid)


@router.get("/{sid}/runs")
def list_runs(sid: str, limit: int = Query(20, ge=1, le=200), user=Depends(_get_user)):
    eng = _get_engine()
    if eng is None:
        return {"runs": []}
    from sqlalchemy import text
    with eng.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT id, started_at, finished_at, status, response_excerpt, "
                "       cost_usd, error "
                "FROM dash.dash_agent_schedule_runs WHERE schedule_id=:id "
                "ORDER BY started_at DESC LIMIT :lim"
            ),
            {"id": sid, "lim": limit},
        ).mappings().all()
    return {"runs": [dict(r) for r in rows]}
