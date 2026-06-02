"""Dash-OS Phase 5 — Channels CRUD + webhooks (Slack/SES/Twilio)."""
from __future__ import annotations

import json as _json
import logging
import secrets
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response, JSONResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/channels", tags=["channels"])


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


# ── Slack workspace CRUD ─────────────────────────────────────────────────
class SlackWorkspaceIn(BaseModel):
    team_id: str
    team_name: Optional[str] = None
    bot_token: str
    bot_user_id: Optional[str] = None
    signing_secret: Optional[str] = None
    default_project_slug: Optional[str] = None


@router.post("/slack/workspaces")
def add_slack_workspace(body: SlackWorkspaceIn, user=Depends(_get_user)):
    if not user or not user.get("is_super_admin"):
        raise HTTPException(403, "super_admin_required")
    eng = _get_engine()
    if eng is None:
        raise HTTPException(503, "db unavailable")
    sid = "sw_" + secrets.token_hex(4)
    from sqlalchemy import text
    with eng.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO dash.dash_slack_workspaces
                  (id, team_id, team_name, bot_token, bot_user_id, signing_secret,
                   default_project_slug, installed_by)
                VALUES (:id, :tid, :tn, :bt, :bu, :ss, :dps, :ib)
                ON CONFLICT (team_id) DO UPDATE
                  SET bot_token=EXCLUDED.bot_token, signing_secret=EXCLUDED.signing_secret,
                      default_project_slug=EXCLUDED.default_project_slug, enabled=true
                """
            ),
            {
                "id": sid, "tid": body.team_id, "tn": body.team_name,
                "bt": body.bot_token, "bu": body.bot_user_id, "ss": body.signing_secret,
                "dps": body.default_project_slug, "ib": user.get("id"),
            },
        )
    return {"ok": True, "id": sid}


@router.get("/slack/workspaces")
def list_slack_workspaces(user=Depends(_get_user)):
    eng = _get_engine()
    if eng is None:
        return {"workspaces": []}
    from sqlalchemy import text
    with eng.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT id, team_id, team_name, default_project_slug, enabled, installed_at "
                "FROM dash.dash_slack_workspaces ORDER BY installed_at DESC"
            )
        ).mappings().all()
    return {"workspaces": [dict(r) for r in rows]}


class SlackRouteIn(BaseModel):
    workspace_id: str
    channel_id: str
    project_slug: str


@router.post("/slack/routes")
def add_slack_route(body: SlackRouteIn, user=Depends(_get_user)):
    eng = _get_engine()
    if eng is None:
        raise HTTPException(503, "db unavailable")
    from sqlalchemy import text
    with eng.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO dash.dash_slack_channel_routes
                  (workspace_id, channel_id, project_slug)
                VALUES (:w, :c, :p)
                ON CONFLICT (workspace_id, channel_id) DO UPDATE
                  SET project_slug = EXCLUDED.project_slug, enabled=true
                """
            ),
            {"w": body.workspace_id, "c": body.channel_id, "p": body.project_slug},
        )
    return {"ok": True}


# ── Slack webhook ───────────────────────────────────────────────────────
@router.post("/slack/events")
async def slack_events(request: Request):
    body_bytes = await request.body()
    try:
        payload = _json.loads(body_bytes.decode("utf-8"))
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)

    # URL verification — respond immediately
    if payload.get("type") == "url_verification":
        return {"challenge": payload.get("challenge", "")}

    # Signature verify per workspace
    team_id = payload.get("team_id") or (payload.get("event") or {}).get("team")
    eng = _get_engine()
    if eng and team_id:
        from sqlalchemy import text
        with eng.connect() as conn:
            row = conn.execute(
                text("SELECT signing_secret FROM dash.dash_slack_workspaces WHERE team_id=:t"),
                {"t": team_id},
            ).first()
        if row and row[0]:
            timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
            signature = request.headers.get("X-Slack-Signature", "")
            from dash.channels.slack import verify_signature
            if not verify_signature(row[0], timestamp, body_bytes, signature):
                return JSONResponse({"error": "bad_signature"}, status_code=401)

    from dash.channels.slack import handle_event
    import asyncio
    asyncio.create_task(asyncio.to_thread(handle_event, payload))
    return {"ok": True}


@router.post("/slack/commands")
async def slack_commands(request: Request):
    form = await request.form()
    payload = dict(form)
    from dash.channels.slack import handle_slash_command
    return handle_slash_command(payload)


# ── Email account CRUD ───────────────────────────────────────────────────
class EmailAccountIn(BaseModel):
    name: str
    inbound_kind: str = "imap"   # 'imap' | 'ses_webhook'
    imap_host: Optional[str] = None
    imap_port: Optional[int] = 993
    imap_user: Optional[str] = None
    imap_pass: Optional[str] = None
    smtp_host: Optional[str] = None
    smtp_port: Optional[int] = 587
    smtp_user: Optional[str] = None
    smtp_pass: Optional[str] = None
    default_project_slug: Optional[str] = None
    subject_prefix_pattern: Optional[str] = None


@router.post("/email/accounts")
def add_email_account(body: EmailAccountIn, user=Depends(_get_user)):
    if not user or not user.get("is_super_admin"):
        raise HTTPException(403, "super_admin_required")
    eng = _get_engine()
    if eng is None:
        raise HTTPException(503, "db unavailable")
    eid = "em_" + secrets.token_hex(4)
    from sqlalchemy import text
    with eng.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO dash.dash_email_accounts
                  (id, name, inbound_kind, imap_host, imap_port, imap_user, imap_pass,
                   smtp_host, smtp_port, smtp_user, smtp_pass,
                   default_project_slug, subject_prefix_pattern)
                VALUES (:id, :nm, :ik, :ih, :ipo, :iu, :ipw,
                        :sh, :spo, :su, :spw,
                        :dps, :spp)
                """
            ),
            {
                "id": eid, "nm": body.name, "ik": body.inbound_kind,
                "ih": body.imap_host, "ipo": body.imap_port,
                "iu": body.imap_user, "ipw": body.imap_pass,
                "sh": body.smtp_host, "spo": body.smtp_port,
                "su": body.smtp_user, "spw": body.smtp_pass,
                "dps": body.default_project_slug,
                "spp": body.subject_prefix_pattern,
            },
        )
    return {"ok": True, "id": eid}


@router.get("/email/accounts")
def list_email_accounts(user=Depends(_get_user)):
    eng = _get_engine()
    if eng is None:
        return {"accounts": []}
    from sqlalchemy import text
    with eng.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT id, name, inbound_kind, imap_user, smtp_user, "
                "       default_project_slug, enabled, created_at "
                "FROM dash.dash_email_accounts ORDER BY created_at DESC"
            )
        ).mappings().all()
    return {"accounts": [dict(r) for r in rows]}


# ── SES inbound webhook ─────────────────────────────────────────────────
@router.post("/email/ses/inbound")
async def ses_inbound(request: Request):
    payload = await request.json()
    from dash.channels.email import handle_ses_webhook
    return handle_ses_webhook(payload)


# ── Voice CRUD ──────────────────────────────────────────────────────────
class VoiceNumberIn(BaseModel):
    phone_number: str
    provider: str = "twilio"
    account_sid: Optional[str] = None
    auth_token: Optional[str] = None
    default_project_slug: Optional[str] = None
    tts_voice: str = "Rachel"


@router.post("/voice/numbers")
def add_voice_number(body: VoiceNumberIn, user=Depends(_get_user)):
    if not user or not user.get("is_super_admin"):
        raise HTTPException(403, "super_admin_required")
    eng = _get_engine()
    if eng is None:
        raise HTTPException(503, "db unavailable")
    vid = "vn_" + secrets.token_hex(4)
    from sqlalchemy import text
    with eng.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO dash.dash_voice_numbers
                  (id, phone_number, provider, account_sid, auth_token,
                   default_project_slug, tts_voice)
                VALUES (:id, :pn, :pr, :as_, :at, :dps, :tv)
                ON CONFLICT (phone_number) DO UPDATE
                  SET account_sid=EXCLUDED.account_sid, auth_token=EXCLUDED.auth_token,
                      default_project_slug=EXCLUDED.default_project_slug
                """
            ),
            {
                "id": vid, "pn": body.phone_number, "pr": body.provider,
                "as_": body.account_sid, "at": body.auth_token,
                "dps": body.default_project_slug, "tv": body.tts_voice,
            },
        )
    return {"ok": True, "id": vid}


@router.get("/voice/numbers")
def list_voice_numbers(user=Depends(_get_user)):
    eng = _get_engine()
    if eng is None:
        return {"numbers": []}
    from sqlalchemy import text
    with eng.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT id, phone_number, provider, default_project_slug, "
                "       enabled, created_at "
                "FROM dash.dash_voice_numbers ORDER BY created_at DESC"
            )
        ).mappings().all()
    return {"numbers": [dict(r) for r in rows]}


# ── Twilio webhooks ─────────────────────────────────────────────────────
@router.post("/voice/twilio/inbound")
async def twilio_inbound(request: Request):
    form = await request.form()
    params = dict(form)
    from dash.channels.voice import handle_inbound_call
    twiml = handle_inbound_call(params)
    return Response(content=twiml, media_type="application/xml")


@router.post("/voice/twilio/process")
async def twilio_process(request: Request):
    form = await request.form()
    params = dict(form)
    from dash.channels.voice import handle_speech_result
    twiml = handle_speech_result(params)
    return Response(content=twiml, media_type="application/xml")


# ── Threads + messages browse ────────────────────────────────────────────
@router.get("/threads")
def list_threads(
    channel_kind: Optional[str] = Query(None),
    project_slug: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    user=Depends(_get_user),
):
    eng = _get_engine()
    if eng is None:
        return {"threads": []}
    from sqlalchemy import text
    with eng.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT id, channel_kind, external_id, project_slug, external_user,
                       subject, status, created_at, last_message_at
                FROM dash.dash_channel_threads
                WHERE (:ck IS NULL OR channel_kind = :ck)
                  AND (:ps IS NULL OR project_slug = :ps)
                ORDER BY last_message_at DESC LIMIT :lim
                """
            ),
            {"ck": channel_kind, "ps": project_slug, "lim": limit},
        ).mappings().all()
    return {"threads": [dict(r) for r in rows]}


@router.get("/threads/{tid}")
def get_thread(tid: str, user=Depends(_get_user)):
    eng = _get_engine()
    if eng is None:
        raise HTTPException(503, "db unavailable")
    from sqlalchemy import text
    with eng.connect() as conn:
        thread = conn.execute(
            text("SELECT * FROM dash.dash_channel_threads WHERE id=:id"),
            {"id": tid},
        ).mappings().first()
        if not thread:
            raise HTTPException(404, "thread_not_found")
        msgs = conn.execute(
            text(
                "SELECT direction, author, body, agent_response_excerpt, "
                "       latency_ms, created_at "
                "FROM dash.dash_channel_messages WHERE thread_id=:id ORDER BY created_at"
            ),
            {"id": tid},
        ).mappings().all()
    return {"thread": dict(thread), "messages": [dict(m) for m in msgs]}
