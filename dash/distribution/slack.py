"""
Slack delivery. Stub-safe when neither SLACK_BOT_TOKEN nor SLACK_WEBHOOK_URL set.

- bot token → files.upload + chat.postMessage (supports file attachment)
- webhook   → POST {text} (no file upload via webhook — limitation noted)
- neither   → stub, logs + returns mode='stub'

If a credential is set but delivery fails → raises (fail-loud).
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

log = logging.getLogger("dash.distribution.slack")


def _env(name: str) -> Optional[str]:
    v = os.getenv(name)
    return v if v else None


def send_slack(
    channel: str,
    text: str,
    file_path: Optional[Path] = None,
) -> dict:
    """Send Slack message, optionally with file attachment.

    Returns:
        {ok: bool, mode: 'live'|'stub', message_id?: str, error?: str,
         would_send_to?: str}
    """
    bot_token = _env("SLACK_BOT_TOKEN")
    webhook = _env("SLACK_WEBHOOK_URL")

    if not bot_token and not webhook:
        log.info(
            "SLACK STUB: would send channel=%s text=%r file=%s",
            channel, text[:200] if text else "", str(file_path) if file_path else None,
        )
        return {
            "ok": True,
            "mode": "stub",
            "would_send_to": channel,
        }

    try:
        import httpx
    except ImportError as e:
        # httpx is part of the runtime deps; treat missing as configuration error.
        raise RuntimeError(f"httpx not available for Slack delivery: {e}")

    # Bot token path — files.upload (+ implicit chat post) or chat.postMessage
    if bot_token:
        chan = channel.lstrip("#") if channel else ""
        if chan.lower().startswith("slack:"):
            chan = chan.split(":", 1)[1]
        if not chan:
            return {"ok": False, "mode": "live", "error": "empty channel"}

        with httpx.Client(timeout=30.0) as client:
            if file_path and Path(file_path).is_file():
                with open(file_path, "rb") as f:
                    files = {"file": (Path(file_path).name, f)}
                    data = {
                        "channels": chan,
                        "initial_comment": text or "",
                        "filename": Path(file_path).name,
                    }
                    r = client.post(
                        "https://slack.com/api/files.upload",
                        headers={"Authorization": f"Bearer {bot_token}"},
                        data=data,
                        files=files,
                    )
                r.raise_for_status()
                resp = r.json()
                if not resp.get("ok"):
                    raise RuntimeError(f"Slack files.upload failed: {resp.get('error')}")
                file_id = (resp.get("file") or {}).get("id")
                log.info("SLACK FILE SENT: channel=%s file=%s", chan, file_id)
                return {"ok": True, "mode": "live", "message_id": file_id}
            else:
                r = client.post(
                    "https://slack.com/api/chat.postMessage",
                    headers={
                        "Authorization": f"Bearer {bot_token}",
                        "Content-Type": "application/json; charset=utf-8",
                    },
                    json={"channel": chan, "text": text or ""},
                )
                r.raise_for_status()
                resp = r.json()
                if not resp.get("ok"):
                    raise RuntimeError(f"Slack chat.postMessage failed: {resp.get('error')}")
                ts = resp.get("ts")
                log.info("SLACK MSG SENT: channel=%s ts=%s", chan, ts)
                return {"ok": True, "mode": "live", "message_id": ts}

    # Webhook fallback — text only (no file upload via webhook)
    note = None
    if file_path:
        note = "webhook mode: file attachment not supported (use SLACK_BOT_TOKEN for files)"
        log.warning(note)

    with httpx.Client(timeout=30.0) as client:
        r = client.post(webhook, json={"text": text or ""})
        r.raise_for_status()

    log.info("SLACK WEBHOOK SENT: channel=%s", channel)
    out = {"ok": True, "mode": "live"}
    if note:
        out["warning"] = note
    return out
