"""
Email delivery via SMTP. Stub-safe when SMTP_HOST is unset.

If SMTP_HOST is missing → returns {ok:True, mode:'stub', would_send_to:[...]}
and logs at INFO. Never raises.

If SMTP_HOST is set and delivery fails → raises (fail-loud, no silent swallow).
"""
from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email import encoders
from pathlib import Path
from typing import List, Optional

log = logging.getLogger("dash.distribution.email")


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    v = os.getenv(name)
    if v is None or v == "":
        return default
    return v


def send_email(
    to: List[str],
    subject: str,
    body: str,
    attachments: Optional[List[Path]] = None,
) -> dict:
    """Send email via SMTP, or return stub result if credentials missing.

    Returns:
        {ok: bool, mode: 'live'|'stub', message_id?: str, error?: str,
         would_send_to?: list, subject?: str, attachments?: list[str]}
    """
    attachments = attachments or []
    to = [t for t in (to or []) if t]

    smtp_host = _env("SMTP_HOST")
    if not smtp_host:
        log.info(
            "EMAIL STUB: would send to=%s subject=%r attachments=%s",
            to, subject, [str(p) for p in attachments],
        )
        return {
            "ok": True,
            "mode": "stub",
            "would_send_to": to,
            "subject": subject,
            "attachments": [str(p) for p in attachments],
        }

    if not to:
        return {"ok": False, "mode": "live", "error": "no recipients"}

    smtp_port = int(_env("SMTP_PORT", "587") or "587")
    smtp_user = _env("SMTP_USER")
    smtp_pass = _env("SMTP_PASS")
    smtp_from = _env("SMTP_FROM") or smtp_user or "noreply@localhost"
    use_tls = (_env("SMTP_USE_TLS", "true") or "true").lower() in ("1", "true", "yes", "on")

    msg = MIMEMultipart()
    msg["From"] = smtp_from
    msg["To"] = ", ".join(to)
    msg["Subject"] = subject
    msg.attach(MIMEText(body or "", "plain", "utf-8"))

    for p in attachments:
        path = Path(p)
        if not path.is_file():
            log.warning("attachment missing, skipping: %s", path)
            continue
        with open(path, "rb") as f:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header(
            "Content-Disposition",
            f'attachment; filename="{path.name}"',
        )
        msg.attach(part)

    # Real send — let exceptions propagate (fail-loud per spec).
    with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
        server.ehlo()
        if use_tls:
            ctx = ssl.create_default_context()
            server.starttls(context=ctx)
            server.ehlo()
        if smtp_user and smtp_pass:
            server.login(smtp_user, smtp_pass)
        server.sendmail(smtp_from, to, msg.as_string())

    message_id = msg.get("Message-ID") or f"<smtp-{abs(hash((tuple(to), subject)))}@dash>"
    log.info("EMAIL SENT: to=%s subject=%r", to, subject)
    return {"ok": True, "mode": "live", "message_id": message_id}
