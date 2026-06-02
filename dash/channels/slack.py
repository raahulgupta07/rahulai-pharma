"""Slack-native channel: signature verify, event dispatch (message/app_mention/im),
slash commands, threaded conversations preserve session_id.

slack_sdk imported gracefully — feature degrades if missing.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


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


def verify_signature(signing_secret: str, timestamp: str, body: bytes, signature: str) -> bool:
    """Slack request signature verification (v0)."""
    try:
        if abs(int(time.time()) - int(timestamp)) > 60 * 5:
            return False
        basestring = f"v0:{timestamp}:{body.decode('utf-8', errors='replace')}"
        my_sig = "v0=" + hmac.new(
            signing_secret.encode("utf-8"), basestring.encode("utf-8"), hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(my_sig, signature)
    except Exception:
        return False


def _get_workspace(team_id: str) -> Optional[Dict[str, Any]]:
    eng = _get_engine()
    if eng is None:
        return None
    try:
        from sqlalchemy import text
        with eng.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT id, bot_token, signing_secret, default_project_slug "
                    "FROM dash.dash_slack_workspaces WHERE team_id=:t AND enabled=true"
                ),
                {"t": team_id},
            ).mappings().first()
        return dict(row) if row else None
    except Exception:
        return None


def handle_event(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Handle Slack Events API payload. Returns {ok, action}."""
    # URL verification challenge
    if payload.get("type") == "url_verification":
        return {"challenge": payload.get("challenge", "")}

    event = payload.get("event") or {}
    event_type = event.get("type")
    team_id = payload.get("team_id") or event.get("team")

    if event_type not in ("message", "app_mention"):
        return {"ok": True, "action": "ignored", "reason": f"event_type={event_type}"}
    # Ignore bot's own messages
    if event.get("bot_id") or event.get("subtype") == "bot_message":
        return {"ok": True, "action": "ignored", "reason": "bot_message"}

    workspace = _get_workspace(team_id) if team_id else None
    if not workspace:
        return {"ok": False, "error": "workspace_not_registered", "team_id": team_id}

    user_id = event.get("user")
    channel_id = event.get("channel")
    thread_ts = event.get("thread_ts") or event.get("ts")
    text = (event.get("text") or "").strip()

    # Strip bot mention prefix
    text = _strip_mention(text)
    if not text:
        return {"ok": True, "action": "ignored", "reason": "empty_text"}

    from dash.channels.common import (
        upsert_thread, log_message, dispatch_to_agent, lookup_project_for_slack,
    )

    project_slug = lookup_project_for_slack(workspace["id"], channel_id) \
        or workspace.get("default_project_slug")
    if not project_slug:
        send_reply(workspace["bot_token"], channel_id, thread_ts,
                   "No Dash project mapped for this channel. Configure in /api/channels/slack.")
        return {"ok": False, "error": "no_project_mapping"}

    thread = upsert_thread(
        "slack", thread_ts, project_slug,
        workspace_id=workspace["id"], channel_id=channel_id, external_user=user_id,
    )
    log_message(thread["id"], "inbound", text, author=user_id, external_msg_id=event.get("ts"))

    result = dispatch_to_agent(project_slug, text, session_id=thread["dash_session_id"])
    reply_text = result.get("text") if result.get("ok") else f"⚠️ {result.get('error')}"
    log_message(
        thread["id"], "outbound", reply_text,
        agent_response_excerpt=reply_text[:500],
        latency_ms=result.get("latency_ms", 0),
    )
    send_reply(workspace["bot_token"], channel_id, thread_ts, reply_text)
    return {"ok": True, "action": "replied", "thread_id": thread["id"]}


def handle_slash_command(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Handle /dash commands. payload from form-encoded Slack post."""
    command = payload.get("command", "")
    text = (payload.get("text") or "").strip()
    team_id = payload.get("team_id")
    user_id = payload.get("user_id")
    channel_id = payload.get("channel_id")

    workspace = _get_workspace(team_id) if team_id else None
    if not workspace:
        return {"response_type": "ephemeral", "text": "Workspace not registered."}

    project_slug = workspace.get("default_project_slug")
    if not project_slug:
        return {"response_type": "ephemeral", "text": "No default project configured."}

    if command in ("/dash", "/dash-ask"):
        if not text:
            return {"response_type": "ephemeral", "text": "Usage: /dash <your question>"}
        from dash.channels.common import dispatch_to_agent
        result = dispatch_to_agent(project_slug, text)
        return {
            "response_type": "in_channel",
            "text": result.get("text", "⚠️ no response") if result.get("ok") else f"⚠️ {result.get('error')}",
        }
    return {"response_type": "ephemeral", "text": f"Unknown command: {command}"}


def _strip_mention(text: str) -> str:
    """Remove leading <@Uxxxx> bot mention."""
    import re
    return re.sub(r"^\s*<@[UW][A-Z0-9]+>\s*", "", text or "")


def send_reply(bot_token: str, channel: str, thread_ts: Optional[str], text: str) -> Dict[str, Any]:
    """Post to chat.postMessage. Best-effort, returns dict."""
    try:
        import httpx
        with httpx.Client(timeout=10.0) as client:
            payload = {"channel": channel, "text": text[:39000]}
            if thread_ts:
                payload["thread_ts"] = thread_ts
            r = client.post(
                "https://slack.com/api/chat.postMessage",
                headers={"Authorization": f"Bearer {bot_token}"},
                json=payload,
            )
            return r.json()
    except Exception as e:
        logger.warning("slack send_reply failed: %s", e)
        return {"ok": False, "error": str(e)}
