"""Email inbound (IMAP poll + SES webhook) and SMTP outbound.

IMAP daemon polls every 60s. Subject regex captures project_slug from
`[project-slug] Subject` pattern. Reply via SMTP preserves Message-ID threading.

Disable IMAP daemon via: EMAIL_POLLER_DISABLED=1 or DAEMONS_DISABLED=1.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from email.message import EmailMessage
from email.parser import BytesParser
from email.policy import default as email_default
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_PREFIX_RE = re.compile(r"^\[([a-z0-9_-]+)\]")
POLL_INTERVAL_S = int(os.getenv("EMAIL_POLL_S", "60"))


def _disabled() -> bool:
    return (
        os.getenv("EMAIL_POLLER_DISABLED") == "1"
        or os.getenv("DAEMONS_DISABLED") == "1"
        or os.getenv("K8S_DAEMON_MODE") == "cronjob"
    )


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


def _list_accounts() -> List[Dict[str, Any]]:
    eng = _get_engine()
    if eng is None:
        return []
    try:
        from sqlalchemy import text
        with eng.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT * FROM dash.dash_email_accounts "
                    "WHERE enabled=true AND inbound_kind='imap'"
                )
            ).mappings().all()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning("list_accounts failed: %s", e)
        return []


def _extract_project_slug(subject: str, pattern: Optional[str], default_slug: Optional[str]) -> Optional[str]:
    try:
        regex = re.compile(pattern) if pattern else DEFAULT_PREFIX_RE
        m = regex.match(subject or "")
        if m and m.groups():
            return m.group(1)
    except Exception:
        pass
    return default_slug


def _strip_quoted(body: str) -> str:
    """Drop email quoted-reply tail (everything after first `On ... wrote:` line)."""
    lines = body.splitlines()
    out = []
    for ln in lines:
        if re.match(r"^On .+ wrote:\s*$", ln):
            break
        if ln.startswith(">"):
            break
        out.append(ln)
    return "\n".join(out).strip()


def handle_inbound_message(
    account: Dict[str, Any], msg_bytes: bytes,
) -> Dict[str, Any]:
    """Parse raw email bytes and dispatch to agent."""
    try:
        msg = BytesParser(policy=email_default).parsebytes(msg_bytes)
    except Exception as e:
        return {"ok": False, "error": f"parse: {e}"}
    subject = (msg["Subject"] or "").strip()
    from_addr = msg["From"] or ""
    message_id = msg["Message-ID"] or ""
    in_reply_to = msg["In-Reply-To"] or msg["References"] or message_id

    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                try:
                    body = part.get_content()
                except Exception:
                    body = part.get_payload(decode=True).decode("utf-8", errors="replace") if part.get_payload(decode=True) else ""
                break
    else:
        try:
            body = msg.get_content()
        except Exception:
            body = msg.get_payload(decode=True).decode("utf-8", errors="replace") if msg.get_payload(decode=True) else ""
    body = _strip_quoted(body or "")

    project_slug = _extract_project_slug(
        subject, account.get("subject_prefix_pattern"), account.get("default_project_slug"),
    )
    if not project_slug:
        return {"ok": False, "error": "no_project_route", "subject": subject}

    from dash.channels.common import upsert_thread, log_message, dispatch_to_agent
    thread = upsert_thread(
        "email", in_reply_to or message_id, project_slug,
        channel_id=account["id"], external_user=from_addr, subject=subject,
    )
    log_message(thread["id"], "inbound", body, author=from_addr, external_msg_id=message_id)

    result = dispatch_to_agent(project_slug, body, session_id=thread["dash_session_id"])
    reply_text = result.get("text") if result.get("ok") else f"⚠️ {result.get('error')}"
    log_message(
        thread["id"], "outbound", reply_text,
        agent_response_excerpt=reply_text[:500],
        latency_ms=result.get("latency_ms", 0),
    )

    # SMTP send reply
    send_result = send_email_reply(
        account, to_addr=from_addr, subject=f"Re: {subject}",
        body=reply_text, in_reply_to=message_id,
    )
    return {"ok": True, "thread_id": thread["id"], "send": send_result}


def send_email_reply(
    account: Dict[str, Any], to_addr: str, subject: str,
    body: str, in_reply_to: Optional[str] = None,
) -> Dict[str, Any]:
    try:
        import smtplib
        msg = EmailMessage()
        msg["From"] = account.get("smtp_user") or account.get("imap_user")
        msg["To"] = to_addr
        msg["Subject"] = subject
        if in_reply_to:
            msg["In-Reply-To"] = in_reply_to
            msg["References"] = in_reply_to
        msg.set_content(body)
        host = account.get("smtp_host")
        port = int(account.get("smtp_port") or 587)
        with smtplib.SMTP(host, port, timeout=15) as smtp:
            smtp.starttls()
            if account.get("smtp_user"):
                smtp.login(account["smtp_user"], account.get("smtp_pass") or "")
            smtp.send_message(msg)
        return {"ok": True}
    except Exception as e:
        logger.warning("smtp send failed: %s", e)
        return {"ok": False, "error": str(e)}


def poll_account_once(account: Dict[str, Any]) -> Dict[str, Any]:
    """Single IMAP poll for one account. Processes UNSEEN messages."""
    try:
        import imaplib
        host = account.get("imap_host")
        port = int(account.get("imap_port") or 993)
        with imaplib.IMAP4_SSL(host, port) as imap:
            imap.login(account["imap_user"], account.get("imap_pass") or "")
            imap.select("INBOX")
            typ, data = imap.search(None, "UNSEEN")
            if typ != "OK":
                return {"ok": False, "error": "search failed"}
            count = 0
            for num in data[0].split():
                typ, msg_data = imap.fetch(num, "(RFC822)")
                if typ == "OK" and msg_data and msg_data[0]:
                    raw = msg_data[0][1]
                    handle_inbound_message(account, raw)
                    count += 1
            return {"ok": True, "processed": count}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def email_poll_loop() -> None:
    if _disabled():
        logger.info("email_poll_loop disabled")
        return
    logger.info("email_poll_loop starting (poll=%ds)", POLL_INTERVAL_S)
    while True:
        try:
            accounts = await asyncio.to_thread(_list_accounts)
            for acct in accounts:
                try:
                    await asyncio.to_thread(poll_account_once, acct)
                except Exception as e:
                    logger.warning("poll %s failed: %s", acct.get("id"), e)
        except Exception as e:
            logger.warning("email_poll_loop iteration failed: %s", e)
        await asyncio.sleep(POLL_INTERVAL_S)


def handle_ses_webhook(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Process SES inbound webhook (assumes raw email in payload['content'])."""
    raw = payload.get("content") or ""
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    # use first SES-enabled account as router
    accounts = _list_accounts()
    ses_acct = next(
        (a for a in _all_accounts() if a.get("inbound_kind") == "ses_webhook"), None,
    )
    if not ses_acct:
        return {"ok": False, "error": "no_ses_account_configured"}
    return handle_inbound_message(ses_acct, raw)


def _all_accounts() -> List[Dict[str, Any]]:
    eng = _get_engine()
    if eng is None:
        return []
    try:
        from sqlalchemy import text
        with eng.connect() as conn:
            rows = conn.execute(
                text("SELECT * FROM dash.dash_email_accounts WHERE enabled=true")
            ).mappings().all()
        return [dict(r) for r in rows]
    except Exception:
        return []
