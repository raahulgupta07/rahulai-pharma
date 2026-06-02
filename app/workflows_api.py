"""Dash-OS Phase 3 — Workflow DAG CRUD + run + SSE stream."""
from __future__ import annotations

import asyncio
import json as _json
import logging
import secrets
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/os/workflows", tags=["dash-os-workflows"])


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


class WorkflowDefIn(BaseModel):
    project_slug: Optional[str] = None
    name: str
    description: Optional[str] = None
    category: Optional[str] = None
    spec: Dict[str, Any]
    trigger_kind: str = "manual"
    cron_expr: Optional[str] = None


class RunRequest(BaseModel):
    inputs: Dict[str, Any] = {}


# ── CRUD ────────────────────────────────────────────────────────────────
@router.get("")
def list_defs(
    project_slug: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    user=Depends(_get_user),
):
    eng = _get_engine()
    if eng is None:
        return {"workflows": []}
    from sqlalchemy import text
    with eng.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT id, name, description, category, project_slug, is_builtin,
                       enabled, trigger_kind, cron_expr, created_at, updated_at
                FROM dash.dash_workflow_defs
                WHERE (CAST(:ps AS TEXT) IS NULL OR project_slug = :ps OR project_slug IS NULL)
                  AND (CAST(:cat AS TEXT) IS NULL OR category = :cat)
                ORDER BY is_builtin DESC, created_at DESC
                """
            ),
            {"ps": project_slug, "cat": category},
        ).mappings().all()
    return {"workflows": [dict(r) for r in rows]}


@router.get("/{wf_id}")
def get_def(wf_id: str, user=Depends(_get_user)):
    eng = _get_engine()
    if eng is None:
        raise HTTPException(503, "db unavailable")
    from sqlalchemy import text
    with eng.connect() as conn:
        row = conn.execute(
            text("SELECT * FROM dash.dash_workflow_defs WHERE id=:id"),
            {"id": wf_id},
        ).mappings().first()
    if not row:
        raise HTTPException(404, "workflow_not_found")
    return dict(row)


@router.post("")
def create_def(body: WorkflowDefIn, user=Depends(_get_user)):
    from dash.workflows.schema import validate
    ok, errors = validate(body.spec)
    if not ok:
        raise HTTPException(400, {"errors": errors})
    eng = _get_engine()
    if eng is None:
        raise HTTPException(503, "db unavailable")
    wid = "wf_" + secrets.token_hex(4)
    from sqlalchemy import text
    with eng.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO dash.dash_workflow_defs
                  (id, project_slug, name, description, category, spec,
                   trigger_kind, cron_expr, created_by)
                VALUES (:id, :ps, :nm, :ds, :cat, CAST(:sp AS jsonb),
                        :tk, :ce, :cb)
                """
            ),
            {
                "id": wid, "ps": body.project_slug, "nm": body.name,
                "ds": body.description, "cat": body.category,
                "sp": _json.dumps(body.spec),
                "tk": body.trigger_kind, "ce": body.cron_expr,
                "cb": user.get("id") if user else None,
            },
        )
    return {"ok": True, "id": wid}


@router.patch("/{wf_id}")
def patch_def(wf_id: str, body: Dict[str, Any], user=Depends(_get_user)):
    eng = _get_engine()
    if eng is None:
        raise HTTPException(503, "db unavailable")
    allowed = {"name", "description", "category", "spec", "enabled",
               "trigger_kind", "cron_expr"}
    fields = {k: v for k, v in body.items() if k in allowed and v is not None}
    if not fields:
        return {"ok": True, "patched": 0}
    if "spec" in fields:
        from dash.workflows.schema import validate
        ok, errors = validate(fields["spec"])
        if not ok:
            raise HTTPException(400, {"errors": errors})
        fields["spec"] = _json.dumps(fields["spec"])
    from sqlalchemy import text
    sets_parts = []
    params = {"id": wf_id}
    for k, v in fields.items():
        if k == "spec":
            sets_parts.append(f"{k}=CAST(:{k} AS jsonb)")
        else:
            sets_parts.append(f"{k}=:{k}")
        params[k] = v
    sets = ", ".join(sets_parts)
    with eng.begin() as conn:
        r = conn.execute(
            text(f"UPDATE dash.dash_workflow_defs SET {sets}, updated_at=now() WHERE id=:id"),
            params,
        )
    return {"ok": r.rowcount > 0, "patched": r.rowcount}


@router.delete("/{wf_id}")
def delete_def(wf_id: str, user=Depends(_get_user)):
    eng = _get_engine()
    if eng is None:
        raise HTTPException(503, "db unavailable")
    from sqlalchemy import text
    with eng.begin() as conn:
        # Block deletion of builtins (super-admin only)
        if user and not user.get("is_super_admin"):
            row = conn.execute(
                text("SELECT is_builtin FROM dash.dash_workflow_defs WHERE id=:id"),
                {"id": wf_id},
            ).first()
            if row and row[0]:
                raise HTTPException(403, "cannot_delete_builtin")
        r = conn.execute(text("DELETE FROM dash.dash_workflow_defs WHERE id=:id"), {"id": wf_id})
    return {"ok": r.rowcount > 0}


# ── Run ────────────────────────────────────────────────────────────────
@router.post("/{wf_id}/run")
async def run_workflow(wf_id: str, body: RunRequest, user=Depends(_get_user)):
    eng = _get_engine()
    if eng is None:
        raise HTTPException(503, "db unavailable")
    from sqlalchemy import text
    with eng.connect() as conn:
        row = conn.execute(
            text("SELECT * FROM dash.dash_workflow_defs WHERE id=:id AND enabled=true"),
            {"id": wf_id},
        ).mappings().first()
    if not row:
        raise HTTPException(404, "workflow_not_found_or_disabled")
    from dash.workflows.runner import execute_workflow
    # Fire-and-forget — returns run_id immediately
    def_dict = dict(row)
    if isinstance(def_dict.get("spec"), str):
        try:
            def_dict["spec"] = _json.loads(def_dict["spec"])
        except Exception:
            pass
    task = asyncio.create_task(
        execute_workflow(
            def_dict, inputs=body.inputs,
            triggered_by=user.get("id") if user else None,
            trigger_kind="manual",
        )
    )
    # Wait briefly to grab run_id
    try:
        result = await asyncio.wait_for(task, timeout=0.5)
        return result
    except asyncio.TimeoutError:
        return {"ok": True, "status": "running", "stream_url": f"/api/workflows/runs/PENDING/stream"}


@router.get("/runs")
def list_runs(
    def_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    user=Depends(_get_user),
):
    eng = _get_engine()
    if eng is None:
        return {"runs": []}
    from sqlalchemy import text
    with eng.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT id, def_id, status, started_at, finished_at, project_slug,
                       triggered_by, trigger_kind, cost_usd, error
                FROM dash.dash_workflow_runs
                WHERE (:did IS NULL OR def_id = :did)
                  AND (:st IS NULL OR status = :st)
                ORDER BY started_at DESC LIMIT :lim
                """
            ),
            {"did": def_id, "st": status, "lim": limit},
        ).mappings().all()
    return {"runs": [dict(r) for r in rows]}


@router.get("/runs/{run_id}")
def get_run(run_id: str, user=Depends(_get_user)):
    eng = _get_engine()
    if eng is None:
        raise HTTPException(503, "db unavailable")
    from sqlalchemy import text
    with eng.connect() as conn:
        run = conn.execute(
            text("SELECT * FROM dash.dash_workflow_runs WHERE id=:id"),
            {"id": run_id},
        ).mappings().first()
        if not run:
            raise HTTPException(404, "run_not_found")
        steps = conn.execute(
            text(
                "SELECT step_id, step_kind, iter, status, latency_ms, "
                "       started_at, finished_at, error "
                "FROM dash.dash_workflow_run_steps WHERE run_id=:id "
                "ORDER BY started_at"
            ),
            {"id": run_id},
        ).mappings().all()
    return {"run": dict(run), "steps": [dict(s) for s in steps]}


@router.get("/runs/{run_id}/stream")
async def stream_run(run_id: str, request: Request, token: Optional[str] = Query(None)):
    from dash.workflows.runner import get_run_bus

    async def gen():
        q = await get_run_bus(run_id)
        yield "data: " + _json.dumps({"event": "connected", "run_id": run_id}) + "\n\n"
        while True:
            if await request.is_disconnected():
                break
            try:
                evt = await asyncio.wait_for(q.get(), timeout=15.0)
                yield "data: " + _json.dumps(evt, default=str) + "\n\n"
                if evt.get("event") in ("run_done", "run_failed"):
                    break
            except asyncio.TimeoutError:
                yield "data: " + _json.dumps({"event": "heartbeat"}) + "\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


# ── Builtin registration on startup ──────────────────────────────────────
@router.on_event("startup")
def _register_builtins_on_startup():
    try:
        from dash.workflows.builtin import register_builtins
        n = register_builtins()
        logger.info("workflow builtins registered: %d", n)
    except Exception as e:
        logger.warning("builtin workflow registration failed: %s", e)
