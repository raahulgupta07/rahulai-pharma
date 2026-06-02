"""
Deliver a scheduled deck: render PPTX (and optional PDF), then route to
each recipient via email or Slack. Logs results back to the schedule row.

Routing rule:
  - looks like email (contains '@')             → send_email
  - starts with '#' or 'slack:'                 → send_slack
  - anything else                               → skipped (recorded as warn)
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from .email import send_email
from .slack import send_slack
from .pdf import render_deck_to_pdf

log = logging.getLogger("dash.distribution.delivery")


def _engine():
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool
    from db import db_url
    return create_engine(db_url, poolclass=NullPool)


def _load_presentation(presentation_id: int) -> Optional[dict]:
    from sqlalchemy import text
    eng = _engine()
    try:
        with eng.connect() as conn:
            row = conn.execute(text(
                "SELECT id, project_slug, title, rendered_pptx_path, pptxgenjs_spec "
                "FROM public.dash_presentations WHERE id = :id"
            ), {"id": presentation_id}).fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "project_slug": row[1],
            "title": row[2],
            "rendered_pptx_path": row[3],
            "pptxgenjs_spec": row[4],
        }
    finally:
        eng.dispose()


def _ensure_pptx(pres: dict) -> Optional[Path]:
    """Return path to the rendered PPTX. Re-renders via existing pipeline if missing."""
    p = pres.get("rendered_pptx_path")
    if p and Path(p).is_file():
        return Path(p)

    spec = pres.get("pptxgenjs_spec")
    if not (isinstance(spec, dict) and (spec.get("slides") or [])):
        log.warning("presentation %s has no spec and no rendered file", pres.get("id"))
        return None

    try:
        from dash.tools.render_pptxgenjs import render_pptx_via_js
    except Exception as e:
        log.warning("render_pptxgenjs import failed: %s", e)
        return None

    out_dir = f"/app/knowledge/_decks/{pres['id']}"
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "deck.pptx")
    try:
        new_path = render_pptx_via_js(spec, out_path)
    except Exception as e:
        log.warning("render_pptx_via_js failed: %s", e)
        return None
    if not new_path or not Path(new_path).is_file():
        return None

    # Persist path so next call skips re-render
    try:
        from sqlalchemy import text
        eng = _engine()
        with eng.connect() as conn:
            conn.execute(text(
                "UPDATE public.dash_presentations "
                "SET rendered_pptx_path = :p, render_engine = 'pptxgenjs' "
                "WHERE id = :id"
            ), {"p": new_path, "id": pres["id"]})
            conn.commit()
        eng.dispose()
    except Exception as e:
        log.warning("persist rendered_pptx_path failed: %s", e)

    return Path(new_path)


def _classify_recipient(r: str) -> str:
    r = (r or "").strip()
    if not r:
        return "unknown"
    if "@" in r and " " not in r:
        return "email"
    if r.startswith("#") or r.lower().startswith("slack:"):
        return "slack"
    return "unknown"


def _record_run(
    schedule_id: Optional[int],
    status: str,
    error: Optional[str] = None,
) -> None:
    if not schedule_id:
        return
    try:
        from sqlalchemy import text
        eng = _engine()
        with eng.connect() as conn:
            conn.execute(text(
                "UPDATE public.dash_deck_schedules "
                "SET last_run_at = :ts, last_status = :st, last_error = :err "
                "WHERE id = :id"
            ), {
                "ts": datetime.utcnow(),
                "st": status[:200],
                "err": (error or "")[:2000] if error else None,
                "id": schedule_id,
            })
            conn.commit()
        eng.dispose()
    except Exception as e:
        log.warning("record_run failed: %s", e)


def deliver_scheduled_deck(presentation_id: int, schedule_row: dict) -> dict:
    """Render deck + ship to recipients per schedule_row.

    schedule_row keys used: id (optional), recipients (list[str]),
    format ('pptx'|'pdf'|'both'), name (optional, used in subject/text).

    Returns dict with overall ok + per-recipient results.
    """
    schedule_id = schedule_row.get("id")
    recipients = schedule_row.get("recipients") or []
    fmt = (schedule_row.get("format") or "pptx").lower()
    name = schedule_row.get("name") or "Scheduled deck"

    pres = _load_presentation(presentation_id)
    if not pres:
        msg = f"presentation {presentation_id} not found"
        _record_run(schedule_id, "error", msg)
        return {"ok": False, "error": msg}

    pptx_path = _ensure_pptx(pres)
    if not pptx_path:
        msg = "could not produce PPTX (no spec / render failed)"
        _record_run(schedule_id, "error", msg)
        return {"ok": False, "error": msg}

    attachments = [pptx_path] if fmt in ("pptx", "both") else []
    pdf_path: Optional[Path] = None
    if fmt in ("pdf", "both"):
        pdf_path = render_deck_to_pdf(pptx_path)
        if pdf_path:
            attachments.append(pdf_path)
        elif fmt == "pdf":
            # PDF requested but soffice unavailable → fall back to PPTX rather
            # than ship nothing. Caller intent was "send the deck".
            log.warning("PDF requested but soffice unavailable; falling back to PPTX")
            attachments = [pptx_path]

    title = pres.get("title") or f"Deck {pres['id']}"
    subject = f"[Dash] {name}: {title}"
    body = (
        f"Scheduled deck delivery: {name}\n"
        f"Project: {pres.get('project_slug')}\n"
        f"Title: {title}\n"
        f"Format: {fmt}\n"
    )
    slack_text = f"*{name}* — {title} ({pres.get('project_slug')})"

    results = []
    any_fail = False
    delivered_to: list[str] = []
    stub_count = 0
    live_count = 0

    # Bucket emails so we send one message per channel/email batch.
    email_targets = [r for r in recipients if _classify_recipient(r) == "email"]
    slack_targets = [r for r in recipients if _classify_recipient(r) == "slack"]
    unknown_targets = [r for r in recipients if _classify_recipient(r) == "unknown"]

    if email_targets:
        try:
            res = send_email(email_targets, subject, body, attachments)
            res["recipients"] = email_targets
            results.append({"channel": "email", **res})
            if res.get("ok"):
                delivered_to.extend(email_targets)
                if res.get("mode") == "stub":
                    stub_count += 1
                else:
                    live_count += 1
            else:
                any_fail = True
        except Exception as e:
            # fail-loud at the per-recipient level, but continue other channels
            log.exception("email send raised")
            any_fail = True
            results.append({
                "channel": "email",
                "recipients": email_targets,
                "ok": False,
                "mode": "live",
                "error": str(e),
            })

    file_for_slack = pdf_path if pdf_path else pptx_path
    for s in slack_targets:
        try:
            res = send_slack(s, slack_text, file_for_slack)
            res["channel_target"] = s
            results.append({"channel": "slack", **res})
            if res.get("ok"):
                delivered_to.append(s)
                if res.get("mode") == "stub":
                    stub_count += 1
                else:
                    live_count += 1
            else:
                any_fail = True
        except Exception as e:
            log.exception("slack send raised for %s", s)
            any_fail = True
            results.append({
                "channel": "slack",
                "channel_target": s,
                "ok": False,
                "mode": "live",
                "error": str(e),
            })

    for u in unknown_targets:
        results.append({
            "channel": "unknown",
            "recipient": u,
            "ok": False,
            "error": "unrecognized recipient (not email, not #channel/slack:)",
        })

    if live_count and not stub_count:
        status = "delivered"
    elif stub_count and not live_count:
        status = "stub"
    elif live_count and stub_count:
        status = "mixed"
    elif any_fail:
        status = "error"
    else:
        status = "no-recipients"

    delivery_status = "stub" if stub_count and not live_count else (
        "delivered" if live_count and not any_fail else status
    )

    err_msg = None
    if any_fail:
        # Find first error string
        for r in results:
            if not r.get("ok") and r.get("error"):
                err_msg = r["error"][:500]
                break

    _record_run(schedule_id, status, err_msg)

    return {
        "ok": not any_fail,
        "status": status,
        "delivery_status": delivery_status,
        "delivered_to": delivered_to,
        "results": results,
        "attachments": [str(p) for p in attachments],
    }
