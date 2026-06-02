"""Voice channel: Twilio webhook → STT → agent → TTS reply via TwiML.

Inbound call hits /api/channels/voice/twilio/inbound with Twilio params.
TwiML response gathers speech, posts transcript to /process, generates
audio reply via Twilio <Say>.
"""
from __future__ import annotations

import logging
import os
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


def _lookup_number(to_number: str) -> Optional[Dict[str, Any]]:
    eng = _get_engine()
    if eng is None:
        return None
    try:
        from sqlalchemy import text
        with eng.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT * FROM dash.dash_voice_numbers "
                    "WHERE phone_number=:p AND enabled=true"
                ),
                {"p": to_number},
            ).mappings().first()
        return dict(row) if row else None
    except Exception:
        return None


def handle_inbound_call(params: Dict[str, Any]) -> str:
    """Return TwiML XML asking caller to speak."""
    to_number = params.get("To", "")
    number = _lookup_number(to_number)
    if not number:
        return _twiml_say("Number not configured. Goodbye.")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Response>'
        f'<Say voice="alice">Hello. You are connected to {number.get("default_project_slug") or "Dash"}. How can I help?</Say>'
        '<Gather input="speech" speechTimeout="auto" action="/api/channels/voice/twilio/process" method="POST">'
        '</Gather>'
        '<Say>I did not catch that. Goodbye.</Say>'
        '</Response>'
    )


def handle_speech_result(params: Dict[str, Any]) -> str:
    """Process speech transcript, dispatch to agent, return TwiML reply."""
    transcript = (params.get("SpeechResult") or "").strip()
    to_number = params.get("To", "")
    call_sid = params.get("CallSid", "")
    from_number = params.get("From", "")
    number = _lookup_number(to_number)
    if not number or not transcript:
        return _twiml_say("I did not catch that. Goodbye.")

    project_slug = number.get("default_project_slug")
    if not project_slug:
        return _twiml_say("No project configured. Goodbye.")

    from dash.channels.common import upsert_thread, log_message, dispatch_to_agent
    thread = upsert_thread(
        "voice", call_sid, project_slug,
        channel_id=number["id"], external_user=from_number,
    )
    log_message(thread["id"], "inbound", transcript, author=from_number)

    result = dispatch_to_agent(project_slug, transcript, session_id=thread["dash_session_id"])
    reply_text = result.get("text") if result.get("ok") else "Sorry, I encountered an error."
    log_message(
        thread["id"], "outbound", reply_text,
        agent_response_excerpt=reply_text[:500],
        latency_ms=result.get("latency_ms", 0),
    )

    # Truncate for voice (long replies don't work over phone)
    spoken = reply_text[:600]

    # ElevenLabs TTS if available — would return audio URL; for now use Twilio <Say>
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Response>'
        f'<Say voice="alice">{_xml_escape(spoken)}</Say>'
        '<Gather input="speech" speechTimeout="auto" action="/api/channels/voice/twilio/process" method="POST">'
        '<Say>Anything else?</Say>'
        '</Gather>'
        '<Say>Goodbye.</Say>'
        '</Response>'
    )


def _twiml_say(text: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Response>'
        f'<Say voice="alice">{_xml_escape(text)}</Say>'
        '</Response>'
    )


def _xml_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
         .replace('"', "&quot;").replace("'", "&apos;")
    )
