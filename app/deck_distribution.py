"""
Deck distribution endpoints.

Endpoints (super-admin OR project editor):
  POST   /api/presentations/{pres_id}/schedule          create schedule
  GET    /api/presentations/{pres_id}/schedules         list per presentation
  PATCH  /api/presentations/schedules/{schedule_id}     toggle/edit
  DELETE /api/presentations/schedules/{schedule_id}
  POST   /api/presentations/schedules/{schedule_id}/run-now

Plus a public-ish helper:
  GET    /api/health/distribution-stub-mode             {stub: bool, ...}

Stub-safe: when no SMTP/Slack creds set, run-now logs the intended send
and records last_status='stub'.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from app.projects import _get_user

log = logging.getLogger("dash.deck_distribution")

router = APIRouter(prefix="/api/presentations", tags=["deck-distribution"])
health_router = APIRouter(prefix="/api/health", tags=["deck-distribution-health"])


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _engine():
    from db import db_url
    return create_engine(db_url, poolclass=NullPool)


def _is_super(user: dict) -> bool:
    try:
        from app.auth import SUPER_ADMIN
        return (user.get("username") or "").lower() == (SUPER_ADMIN or "").lower()
    except Exception:
        return False


def _check_perm(user: dict, project_slug: str, role: str = "editor") -> None:
    """Allow super-admin OR project member with required role."""
    if _is_super(user):
        return
    try:
        from app.auth import check_project_permission
        ok = check_project_permission(user, project_slug, required_role=role)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(403, f"permission check failed: {e}")
    if not ok:
        raise HTTPException(403, f"requires {role} on project {project_slug}")


def _load_pres_slug(pres_id: int) -> Optional[str]:
    eng = _engine()
    try:
        with eng.connect() as conn:
            row = conn.execute(text(
                "SELECT project_slug FROM public.dash_presentations WHERE id = :id"
            ), {"id": pres_id}).fetchone()
        return row[0] if row else None
    finally:
        eng.dispose()


def _row_to_dict(row) -> dict:
    if row is None:
        return {}
    recipients = row[5]
    if isinstance(recipients, str):
        try:
            recipients = json.loads(recipients)
        except Exception:
            recipients = []
    return {
        "id": row[0],
        "project_slug": row[1],
        "presentation_id": row[2],
        "name": row[3],
        "cron": row[4],
        "recipients": recipients or [],
        "channel": row[6],
        "format": row[7],
        "enabled": bool(row[8]),
        "last_run_at": row[9].isoformat() if row[9] else None,
        "last_status": row[10],
        "last_error": row[11],
        "created_by": row[12],
        "created_at": row[13].isoformat() if row[13] else None,
    }


_SELECT_COLS = (
    "id, project_slug, presentation_id, name, cron, recipients, channel, "
    "format, enabled, last_run_at, last_status, last_error, created_by, created_at"
)


# ---------------------------------------------------------------------------
# request models
# ---------------------------------------------------------------------------

class ScheduleCreate(BaseModel):
    name: str
    cron: str
    recipients: list[str]
    channel: str = "email"
    format: str = "pptx"
    enabled: bool = True


class SchedulePatch(BaseModel):
    name: Optional[str] = None
    cron: Optional[str] = None
    recipients: Optional[list[str]] = None
    channel: Optional[str] = None
    format: Optional[str] = None
    enabled: Optional[bool] = None


# ---------------------------------------------------------------------------
# endpoints
# ---------------------------------------------------------------------------

@router.post("/{pres_id}/schedule")
def create_schedule(pres_id: int, body: ScheduleCreate, request: Request):
    user = _get_user(request)
    slug = _load_pres_slug(pres_id)
    if not slug:
        raise HTTPException(404, "presentation not found")
    _check_perm(user, slug, role="editor")

    channel = (body.channel or "email").lower()
    if channel not in ("email", "slack", "both"):
        raise HTTPException(400, "channel must be email|slack|both")
    fmt = (body.format or "pptx").lower()
    if fmt not in ("pptx", "pdf", "both"):
        raise HTTPException(400, "format must be pptx|pdf|both")

    recipients = [r.strip() for r in (body.recipients or []) if r and r.strip()]
    if not recipients:
        raise HTTPException(400, "recipients must be non-empty")

    eng = _engine()
    try:
        with eng.connect() as conn:
            row = conn.execute(text(
                "INSERT INTO public.dash_deck_schedules "
                "(project_slug, presentation_id, name, cron, recipients, channel, format, enabled, created_by) "
                "VALUES (:slug, :pid, :name, :cron, CAST(:rcp AS jsonb), :ch, :fmt, :en, :uid) "
                f"RETURNING {_SELECT_COLS}"
            ), {
                "slug": slug,
                "pid": pres_id,
                "name": body.name[:200],
                "cron": body.cron[:100],
                "rcp": json.dumps(recipients),
                "ch": channel,
                "fmt": fmt,
                "en": bool(body.enabled),
                "uid": user.get("user_id"),
            }).fetchone()
            conn.commit()
        return _row_to_dict(row)
    finally:
        eng.dispose()


@router.get("/{pres_id}/schedules")
def list_schedules(pres_id: int, request: Request):
    user = _get_user(request)
    slug = _load_pres_slug(pres_id)
    if not slug:
        raise HTTPException(404, "presentation not found")
    _check_perm(user, slug, role="editor")

    eng = _engine()
    try:
        with eng.connect() as conn:
            rows = conn.execute(text(
                f"SELECT {_SELECT_COLS} FROM public.dash_deck_schedules "
                "WHERE presentation_id = :pid ORDER BY created_at DESC"
            ), {"pid": pres_id}).fetchall()
        return {"schedules": [_row_to_dict(r) for r in rows]}
    finally:
        eng.dispose()


def _load_schedule(schedule_id: int) -> Optional[dict]:
    eng = _engine()
    try:
        with eng.connect() as conn:
            row = conn.execute(text(
                f"SELECT {_SELECT_COLS} FROM public.dash_deck_schedules WHERE id = :id"
            ), {"id": schedule_id}).fetchone()
        return _row_to_dict(row) if row else None
    finally:
        eng.dispose()


@router.patch("/schedules/{schedule_id}")
def patch_schedule(schedule_id: int, body: SchedulePatch, request: Request):
    user = _get_user(request)
    sched = _load_schedule(schedule_id)
    if not sched:
        raise HTTPException(404, "schedule not found")
    _check_perm(user, sched["project_slug"], role="editor")

    sets = []
    params: dict = {"id": schedule_id}
    if body.name is not None:
        sets.append("name = :name")
        params["name"] = body.name[:200]
    if body.cron is not None:
        sets.append("cron = :cron")
        params["cron"] = body.cron[:100]
    if body.recipients is not None:
        recipients = [r.strip() for r in body.recipients if r and r.strip()]
        sets.append("recipients = CAST(:rcp AS jsonb)")
        params["rcp"] = json.dumps(recipients)
    if body.channel is not None:
        ch = body.channel.lower()
        if ch not in ("email", "slack", "both"):
            raise HTTPException(400, "channel must be email|slack|both")
        sets.append("channel = :ch")
        params["ch"] = ch
    if body.format is not None:
        fmt = body.format.lower()
        if fmt not in ("pptx", "pdf", "both"):
            raise HTTPException(400, "format must be pptx|pdf|both")
        sets.append("format = :fmt")
        params["fmt"] = fmt
    if body.enabled is not None:
        sets.append("enabled = :en")
        params["en"] = bool(body.enabled)

    if not sets:
        return sched  # nothing to update

    eng = _engine()
    try:
        with eng.connect() as conn:
            row = conn.execute(text(
                f"UPDATE public.dash_deck_schedules SET {', '.join(sets)} "
                f"WHERE id = :id RETURNING {_SELECT_COLS}"
            ), params).fetchone()
            conn.commit()
        return _row_to_dict(row)
    finally:
        eng.dispose()


@router.delete("/schedules/{schedule_id}")
def delete_schedule(schedule_id: int, request: Request):
    user = _get_user(request)
    sched = _load_schedule(schedule_id)
    if not sched:
        raise HTTPException(404, "schedule not found")
    _check_perm(user, sched["project_slug"], role="editor")

    eng = _engine()
    try:
        with eng.connect() as conn:
            conn.execute(text(
                "DELETE FROM public.dash_deck_schedules WHERE id = :id"
            ), {"id": schedule_id})
            conn.commit()
        return {"ok": True, "deleted": schedule_id}
    finally:
        eng.dispose()


@router.post("/schedules/{schedule_id}/run-now")
def run_schedule_now(schedule_id: int, request: Request):
    user = _get_user(request)
    sched = _load_schedule(schedule_id)
    if not sched:
        raise HTTPException(404, "schedule not found")
    _check_perm(user, sched["project_slug"], role="editor")

    from dash.distribution import deliver_scheduled_deck
    try:
        result = deliver_scheduled_deck(sched["presentation_id"], sched)
    except Exception as e:
        # Fail-loud per spec: surface real delivery errors.
        log.exception("deliver_scheduled_deck failed")
        raise HTTPException(500, f"delivery failed: {e}")

    return {
        "ok": result.get("ok", False),
        "status": result.get("status"),
        "delivery_status": result.get("delivery_status"),
        "delivered_to": result.get("delivered_to", []),
        "results": result.get("results", []),
        "attachments": result.get("attachments", []),
    }


# ---------------------------------------------------------------------------
# stub-mode health
# ---------------------------------------------------------------------------

@health_router.get("/distribution-stub-mode")
def distribution_stub_mode():
    smtp = bool(os.getenv("SMTP_HOST"))
    slack_bot = bool(os.getenv("SLACK_BOT_TOKEN"))
    slack_hook = bool(os.getenv("SLACK_WEBHOOK_URL"))
    return {
        "stub": (not smtp) and (not slack_bot) and (not slack_hook),
        "smtp_configured": smtp,
        "slack_configured": slack_bot or slack_hook,
    }


# Combined export: app/main.py only needs to include one router.
def _build_combined_router() -> APIRouter:
    r = APIRouter()
    r.include_router(router)
    r.include_router(health_router)
    return r


combined_router = _build_combined_router()
