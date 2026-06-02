"""Shared helpers for channel modules: thread upsert, message logging,
agent dispatch via internal HTTP."""
from __future__ import annotations

import json as _json
import logging
import os
import secrets
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

INTERNAL_BASE = os.getenv("DASH_INTERNAL_URL", "http://127.0.0.1:8000")


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


def upsert_thread(
    channel_kind: str, external_id: str, project_slug: str,
    workspace_id: Optional[str] = None, channel_id: Optional[str] = None,
    external_user: Optional[str] = None, subject: Optional[str] = None,
) -> Dict[str, Any]:
    eng = _get_engine()
    thread_id = "thd_" + secrets.token_hex(4)
    if eng is None:
        return {"id": thread_id, "is_new": True, "dash_session_id": None}
    try:
        from sqlalchemy import text
        with eng.begin() as conn:
            existing = conn.execute(
                text(
                    "SELECT id, dash_session_id FROM dash.dash_channel_threads "
                    "WHERE channel_kind=:ck AND external_id=:eid"
                ),
                {"ck": channel_kind, "eid": external_id},
            ).first()
            if existing:
                conn.execute(
                    text(
                        "UPDATE dash.dash_channel_threads SET last_message_at=now() "
                        "WHERE id=:id"
                    ),
                    {"id": existing[0]},
                )
                return {"id": existing[0], "is_new": False, "dash_session_id": existing[1]}
            session_id = "sess_" + secrets.token_hex(8)
            conn.execute(
                text(
                    """
                    INSERT INTO dash.dash_channel_threads
                      (id, channel_kind, external_id, workspace_id, channel_id,
                       project_slug, dash_session_id, external_user, subject)
                    VALUES (:id, :ck, :eid, :ws, :ch, :ps, :ss, :eu, :sub)
                    """
                ),
                {
                    "id": thread_id, "ck": channel_kind, "eid": external_id,
                    "ws": workspace_id, "ch": channel_id, "ps": project_slug,
                    "ss": session_id, "eu": external_user, "sub": subject,
                },
            )
            return {"id": thread_id, "is_new": True, "dash_session_id": session_id}
    except Exception as e:
        logger.warning("upsert_thread failed: %s", e)
        return {"id": thread_id, "is_new": True, "dash_session_id": None}


def log_message(
    thread_id: str, direction: str, body: str,
    author: Optional[str] = None, external_msg_id: Optional[str] = None,
    attachments: Optional[Dict[str, Any]] = None,
    agent_response_excerpt: Optional[str] = None, latency_ms: int = 0,
) -> None:
    eng = _get_engine()
    if eng is None:
        return
    try:
        from sqlalchemy import text
        with eng.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO dash.dash_channel_messages
                      (thread_id, direction, external_msg_id, author, body,
                       attachments, agent_response_excerpt, latency_ms)
                    VALUES (:tid, :dir, :emid, :au, :bd, CAST(:att AS jsonb),
                            :are, :lat)
                    """
                ),
                {
                    "tid": thread_id, "dir": direction, "emid": external_msg_id,
                    "au": author, "bd": (body or "")[:50000],
                    "att": _json.dumps(attachments) if attachments else None,
                    "are": (agent_response_excerpt or "")[:5000] if agent_response_excerpt else None,
                    "lat": latency_ms,
                },
            )
    except Exception as e:
        logger.warning("log_message failed: %s", e)


def dispatch_to_agent(
    project_slug: str, message: str, session_id: Optional[str] = None,
    timeout_s: int = 120,
) -> Dict[str, Any]:
    """POST to internal chat endpoint. Returns {ok, text, latency_ms}.
    Best-effort, never raises."""
    started = time.time()
    try:
        import httpx
        url = f"{INTERNAL_BASE}/api/projects/{project_slug}/chat"
        token = os.getenv("INTERNAL_CHANNEL_TOKEN") or os.getenv("DASH_SYSTEM_TOKEN")
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        payload = {"message": message}
        if session_id:
            payload["session_id"] = session_id
        with httpx.Client(timeout=timeout_s) as client:
            resp = client.post(url, json=payload, headers=headers)
        latency_ms = int((time.time() - started) * 1000)
        if resp.status_code >= 400:
            return {"ok": False, "error": f"http {resp.status_code}", "latency_ms": latency_ms}
        text = ""
        try:
            j = resp.json()
            text = j.get("response") or j.get("text") or j.get("content") or str(j)[:2000]
        except Exception:
            text = resp.text[:2000]
        return {"ok": True, "text": text, "latency_ms": latency_ms}
    except Exception as e:
        latency_ms = int((time.time() - started) * 1000)
        return {"ok": False, "error": str(e), "latency_ms": latency_ms}


def lookup_project_for_slack(workspace_id: str, channel_id: str) -> Optional[str]:
    eng = _get_engine()
    if eng is None:
        return None
    try:
        from sqlalchemy import text
        with eng.connect() as conn:
            # specific channel route?
            row = conn.execute(
                text(
                    "SELECT project_slug FROM dash.dash_slack_channel_routes "
                    "WHERE workspace_id=:ws AND channel_id=:ch AND enabled=true"
                ),
                {"ws": workspace_id, "ch": channel_id},
            ).first()
            if row:
                return row[0]
            row = conn.execute(
                text(
                    "SELECT default_project_slug FROM dash.dash_slack_workspaces "
                    "WHERE id=:id"
                ),
                {"id": workspace_id},
            ).first()
            return row[0] if row else None
    except Exception:
        return None
