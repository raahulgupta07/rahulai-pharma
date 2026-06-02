"""
HITL (Human-in-the-Loop) FastAPI router.

Endpoints under ``/api/hitl``:
- GET  /pending                      — list user's pending requests
- POST /{run_id}/respond             — approve/reject a confirmation
- POST /{run_id}/input               — submit user_input form data
- POST /{run_id}/external-result     — deliver external execution result
- GET  /stream                       — SSE feed of new pending events (15s heartbeat)

Reuses ``app.auth`` token validation. SSE supports ``?token=<jwt>`` query
fallback because EventSource cannot send Authorization headers.

A background sweeper started on import marks pending rows as expired every
30 seconds.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.engine import Engine

from dash.agentic.hitl import get_pending_queue

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/hitl", tags=["hitl"])

_HEARTBEAT_S = 15.0
_SWEEPER_INTERVAL_S = 30.0


# ── Engine accessor ─────────────────────────────────────────────────────
def _engine() -> Engine:
    try:
        from db.session import get_sql_engine  # type: ignore
        return get_sql_engine()
    except Exception:
        try:
            from db import get_sql_engine  # type: ignore
            return get_sql_engine()
        except Exception:
            from db import db_url  # type: ignore
            from sqlalchemy import create_engine
            from sqlalchemy.pool import NullPool
            return create_engine(db_url, poolclass=NullPool)


# ── Auth helpers (mirrors app/sim_api.py) ───────────────────────────────
def _get_user(request: Request) -> dict:
    user = getattr(getattr(request, "state", None), "user", None)
    if not user:
        try:
            from app.auth import get_current_user  # type: ignore
            user = get_current_user(request)
        except Exception:
            user = None
    if not user:
        raise HTTPException(401, "Not authenticated")
    return user


def _get_user_with_token_fallback(request: Request, token: Optional[str] = None) -> dict:
    user = getattr(getattr(request, "state", None), "user", None)
    if not user:
        try:
            from app.auth import get_current_user, validate_token, _validate_api_key  # type: ignore
            user = get_current_user(request)
            if not user and token:
                if token.startswith("dash-key-"):
                    user = _validate_api_key(token)
                else:
                    user = validate_token(token)
        except Exception:
            user = None
    if not user:
        raise HTTPException(401, "Not authenticated")
    return user


def _user_id(user: dict) -> Optional[int]:
    raw = user.get("user_id") or user.get("id")
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _is_admin_of_project(user: dict, project_slug: Optional[str]) -> bool:
    """Best-effort project-admin check; returns False if helper unavailable."""
    if not project_slug:
        return False
    try:
        from app.auth import check_project_permission  # type: ignore
        return bool(check_project_permission(user, project_slug, required_role="admin"))
    except Exception:
        return False


# ── Request bodies ──────────────────────────────────────────────────────
class RespondBody(BaseModel):
    decision: str  # "approve" | "reject"
    note: Optional[str] = None


class InputBody(BaseModel):
    data: Dict[str, Any]


class ExternalResultBody(BaseModel):
    result: Dict[str, Any]


# ── Helpers ─────────────────────────────────────────────────────────────
def _fetch_row(run_id: str) -> Optional[Dict[str, Any]]:
    eng = _engine()
    with eng.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT run_id, project_slug, user_id, agent_name, action_type,
                       payload, status, response, created_at, expires_at,
                       responded_at, responded_by
                  FROM dash.dash_hitl_pending
                 WHERE run_id = :rid
                """
            ),
            {"rid": run_id},
        ).mappings().first()
        return dict(row) if row else None


def _ensure_authorized(user: dict, row: Dict[str, Any]) -> None:
    uid = _user_id(user)
    if row.get("user_id") is not None and uid is not None and int(row["user_id"]) == uid:
        return
    if _is_admin_of_project(user, row.get("project_slug")):
        return
    # If row has no owner, allow the requester (best-effort gating).
    if row.get("user_id") is None:
        return
    raise HTTPException(403, "Not allowed to respond to this HITL request")


async def _push_to_queue(run_id: str, msg: Dict[str, Any]) -> bool:
    q = get_pending_queue(run_id)
    if q is None:
        return False
    try:
        await q.put(msg)
        return True
    except Exception:
        logger.exception("hitl: failed to push response to queue %s", run_id)
        return False


# ── Endpoints ───────────────────────────────────────────────────────────
@router.get("/pending")
def list_pending(request: Request, project_slug: Optional[str] = None, limit: int = 20):
    user = _get_user(request)
    uid = _user_id(user)
    eng = _engine()
    limit = max(1, min(100, int(limit)))
    with eng.connect() as conn:
        params: Dict[str, Any] = {"uid": uid, "lim": limit}
        where = ["status = 'pending'"]
        if uid is not None:
            where.append("(user_id = :uid OR user_id IS NULL)")
        if project_slug:
            where.append("project_slug = :ps")
            params["ps"] = project_slug
        sql = (
            "SELECT run_id, project_slug, user_id, agent_name, action_type, "
            "payload, status, created_at, expires_at "
            "FROM dash.dash_hitl_pending WHERE "
            + " AND ".join(where)
            + " ORDER BY created_at DESC LIMIT :lim"
        )
        rows = conn.execute(text(sql), params).mappings().all()
    return {"pending": [dict(r) for r in rows]}


@router.post("/{run_id}/respond")
async def respond(run_id: str, body: RespondBody, request: Request):
    user = _get_user(request)
    row = _fetch_row(run_id)
    if not row:
        raise HTTPException(404, "HITL request not found")
    if row["status"] != "pending":
        if row["status"] == "expired":
            raise HTTPException(410, "HITL request expired")
        raise HTTPException(409, f"HITL request already {row['status']}")
    _ensure_authorized(user, row)
    if body.decision not in ("approve", "reject"):
        raise HTTPException(400, "decision must be 'approve' or 'reject'")

    new_status = "approved" if body.decision == "approve" else "rejected"
    eng = _engine()
    with eng.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE dash.dash_hitl_pending
                   SET status = :st,
                       response = CAST(:rsp AS jsonb),
                       responded_at = now(),
                       responded_by = :uid
                 WHERE run_id = :rid AND status = 'pending'
                """
            ),
            {
                "st": new_status,
                "rsp": json.dumps({"decision": body.decision, "note": body.note}),
                "uid": _user_id(user),
                "rid": run_id,
            },
        )
    delivered = await _push_to_queue(run_id, {"decision": body.decision, "note": body.note})
    return {"ok": True, "status": new_status, "delivered_in_process": delivered}


@router.post("/{run_id}/input")
async def submit_input(run_id: str, body: InputBody, request: Request):
    user = _get_user(request)
    row = _fetch_row(run_id)
    if not row:
        raise HTTPException(404, "HITL request not found")
    if row["status"] != "pending":
        if row["status"] == "expired":
            raise HTTPException(410, "HITL request expired")
        raise HTTPException(409, f"HITL request already {row['status']}")
    _ensure_authorized(user, row)

    eng = _engine()
    with eng.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE dash.dash_hitl_pending
                   SET status = 'approved',
                       response = CAST(:rsp AS jsonb),
                       responded_at = now(),
                       responded_by = :uid
                 WHERE run_id = :rid AND status = 'pending'
                """
            ),
            {
                "rsp": json.dumps({"data": body.data}),
                "uid": _user_id(user),
                "rid": run_id,
            },
        )
    delivered = await _push_to_queue(run_id, {"data": body.data})
    return {"ok": True, "delivered_in_process": delivered}


@router.post("/{run_id}/external-result")
async def external_result(run_id: str, body: ExternalResultBody, request: Request):
    user = _get_user(request)
    row = _fetch_row(run_id)
    if not row:
        raise HTTPException(404, "HITL request not found")
    if row["status"] not in ("pending", "external_done"):
        if row["status"] == "expired":
            raise HTTPException(410, "HITL request expired")
        raise HTTPException(409, f"HITL request already {row['status']}")
    _ensure_authorized(user, row)

    eng = _engine()
    with eng.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE dash.dash_hitl_pending
                   SET status = 'external_done',
                       response = CAST(:rsp AS jsonb),
                       responded_at = now(),
                       responded_by = :uid
                 WHERE run_id = :rid
                """
            ),
            {
                "rsp": json.dumps({"result": body.result}),
                "uid": _user_id(user),
                "rid": run_id,
            },
        )
    delivered = await _push_to_queue(run_id, {"result": body.result})
    return {"ok": True, "delivered_in_process": delivered}


@router.get("/stream")
async def stream(request: Request, token: Optional[str] = None):
    user = _get_user_with_token_fallback(request, token)
    uid = _user_id(user)

    async def gen():
        last_seen_id: Optional[str] = None
        last_heartbeat = time.monotonic()
        eng = _engine()
        # Initial snapshot of newest pending rows for this user.
        try:
            with eng.connect() as conn:
                params: Dict[str, Any] = {"uid": uid}
                where = ["status = 'pending'"]
                if uid is not None:
                    where.append("(user_id = :uid OR user_id IS NULL)")
                sql = (
                    "SELECT run_id, project_slug, agent_name, action_type, "
                    "payload, created_at, expires_at "
                    "FROM dash.dash_hitl_pending WHERE "
                    + " AND ".join(where)
                    + " ORDER BY created_at DESC LIMIT 1"
                )
                row = conn.execute(text(sql), params).mappings().first()
                if row:
                    last_seen_id = row["run_id"]
        except Exception:
            logger.exception("hitl stream: initial snapshot failed")

        yield f": connected\n\n"

        while True:
            if await request.is_disconnected():
                break
            try:
                with eng.connect() as conn:
                    params = {"uid": uid}
                    where = ["status = 'pending'"]
                    if uid is not None:
                        where.append("(user_id = :uid OR user_id IS NULL)")
                    if last_seen_id:
                        where.append(
                            "created_at > (SELECT created_at FROM dash.dash_hitl_pending WHERE run_id = :lid)"
                        )
                        params["lid"] = last_seen_id
                    sql = (
                        "SELECT run_id, project_slug, agent_name, action_type, "
                        "payload, created_at, expires_at "
                        "FROM dash.dash_hitl_pending WHERE "
                        + " AND ".join(where)
                        + " ORDER BY created_at ASC LIMIT 25"
                    )
                    rows = conn.execute(text(sql), params).mappings().all()
                for r in rows:
                    payload = dict(r)
                    yield f"event: pending\ndata: {json.dumps(payload, default=str)}\n\n"
                    last_seen_id = payload["run_id"]
            except Exception:
                logger.exception("hitl stream: poll failed")

            now = time.monotonic()
            if now - last_heartbeat >= _HEARTBEAT_S:
                yield f": heartbeat\n\n"
                last_heartbeat = now
            await asyncio.sleep(2.0)

    return StreamingResponse(gen(), media_type="text/event-stream")


# ── Background sweeper ─────────────────────────────────────────────────
async def _sweeper_loop():
    while True:
        try:
            eng = _engine()
            with eng.begin() as conn:
                conn.execute(
                    text(
                        """
                        UPDATE dash.dash_hitl_pending
                           SET status = 'expired', responded_at = now()
                         WHERE status = 'pending' AND expires_at < now()
                        """
                    )
                )
        except Exception:
            logger.exception("hitl sweeper: failed")
        await asyncio.sleep(_SWEEPER_INTERVAL_S)


def _start_sweeper():
    if os.getenv("HITL_SWEEPER_DISABLED") == "1":
        return
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(_sweeper_loop())
            return
    except RuntimeError:
        pass
    # No running loop at import time — register a deferred startup hook.
    @router.on_event("startup")
    async def _on_startup():  # pragma: no cover - trivial
        asyncio.create_task(_sweeper_loop())


_start_sweeper()
