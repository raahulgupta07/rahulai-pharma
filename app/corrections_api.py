"""Correction-learning loop FastAPI router.

Endpoints under ``/api/corrections``:
- POST   /record                       — save a correction + schedule rule extraction
- GET    /rules                        — list active rules (with filters)
- POST   /rules/{rule_id}/toggle       — flip active flag
- DELETE /rules/{rule_id}              — delete rule
- GET    /history                      — recent corrections feed
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/corrections", tags=["corrections"])


def _get_user(request: Request) -> Dict[str, Any]:
    user = getattr(getattr(request, "state", None), "user", None)
    if not user:
        try:
            from app.auth import get_current_user  # type: ignore
            user = get_current_user(request)
        except Exception:
            user = None
    if not user:
        # corrections API is optional auth — allow anonymous for embeds
        return {"id": None, "username": None}
    return user


class RecordBody(BaseModel):
    project: Optional[str] = None
    run_id: Optional[str] = None
    agent_name: Optional[str] = None
    original: str
    edited: str


def _extract_rule_task(correction_id: int) -> None:
    try:
        from dash.learning.corrections import extract_rule_from_diff
        extract_rule_from_diff(correction_id)
    except Exception:
        logger.exception("corrections background extract failed")


@router.post("/record")
def record(body: RecordBody, request: Request, background: BackgroundTasks):
    from dash.learning.corrections import record_correction

    if (body.original or "") == (body.edited or ""):
        return {"ok": True, "correction_id": None, "skipped": "no_change"}

    user = _get_user(request)
    username = user.get("username") if user else None
    cid = record_correction(
        project=body.project,
        run_id=body.run_id,
        agent_name=body.agent_name,
        original=body.original,
        edited=body.edited,
        user=username,
    )
    if cid is None:
        raise HTTPException(500, "Failed to record correction")

    background.add_task(_extract_rule_task, cid)
    return {"ok": True, "correction_id": cid}


@router.get("/rules")
def get_rules(
    project: Optional[str] = Query(None),
    scope: Optional[str] = Query(None),
    scope_target: Optional[str] = Query(None),
    include_inactive: bool = Query(True),
):
    from dash.learning.corrections import list_rules
    rules = list_rules(
        project=project, scope=scope, scope_target=scope_target,
        include_inactive=include_inactive,
    )
    return {"rules": rules, "count": len(rules)}


@router.post("/rules/{rule_id}/toggle")
def toggle(rule_id: int):
    from dash.learning.corrections import toggle_rule
    new_state = toggle_rule(rule_id)
    if new_state is None:
        raise HTTPException(404, "Rule not found")
    return {"ok": True, "active": new_state}


@router.delete("/rules/{rule_id}")
def delete(rule_id: int):
    from dash.learning.corrections import delete_rule
    ok = delete_rule(rule_id)
    if not ok:
        raise HTTPException(404, "Rule not found")
    return {"ok": True}


@router.get("/history")
def history(
    project: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
):
    from dash.learning.corrections import list_recent_corrections
    rows = list_recent_corrections(project=project, limit=limit)
    return {"corrections": rows, "count": len(rows)}
