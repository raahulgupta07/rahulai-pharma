"""Daily digest sender — SMTP + Slack — and APScheduler-style poller.

Per-project daily digest delivery:
  - Markdown body + plain-text fallback for SMTP
  - Slack Block Kit blocks for webhook
  - Polling job (every 5 min) checks each project's digest_time_utc
    and fires send_digest(slug) within a +/- 2 min window if not sent today

Endpoints (all under /api/projects/{slug}/digest/*):
  GET    /config        viewer+   returns config + env-availability flags
  POST   /config        editor+   persist config to dash_projects
  POST   /test          editor+   fires send_digest(slug) immediately

Env vars (no secrets in code):
  SMTP_HOST, SMTP_PORT (default 587), SMTP_USER, SMTP_PASS, SMTP_FROM,
  SMTP_TLS (default 'true'), SLACK_WEBHOOK_URL.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import smtplib
import ssl
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import text

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Digest"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _engine():
    from db.session import get_sql_engine
    return get_sql_engine()


_write_engine = None


def _write_eng():
    """Bootstrap engine that bypasses the dash-schema write guard.

    Used for ALTER TABLE on public.dash_projects and UPDATE writes to digest
    columns. Mirrors the pattern in app/auth.py.
    """
    global _write_engine
    if _write_engine is not None:
        return _write_engine
    from sqlalchemy import create_engine as _ce
    from sqlalchemy.pool import NullPool
    from db.url import db_url
    _write_engine = _ce(db_url, poolclass=NullPool)
    return _write_engine


def _ensure_columns() -> None:
    """Idempotent in-app migration mirror of 014_project_digest.sql.

    Uses a bootstrap engine that bypasses the dash-schema write guard so
    we can ALTER public.dash_projects.
    """
    try:
        with _write_eng().begin() as conn:
            conn.execute(text(
                "ALTER TABLE public.dash_projects "
                "  ADD COLUMN IF NOT EXISTS digest_enabled BOOLEAN DEFAULT FALSE, "
                "  ADD COLUMN IF NOT EXISTS digest_email_to TEXT, "
                "  ADD COLUMN IF NOT EXISTS digest_slack_enabled BOOLEAN DEFAULT FALSE, "
                "  ADD COLUMN IF NOT EXISTS digest_time_utc TEXT DEFAULT '08:00', "
                "  ADD COLUMN IF NOT EXISTS last_digest_sent_at TIMESTAMPTZ, "
                "  ADD COLUMN IF NOT EXISTS last_digest_error TEXT"
            ))
    except Exception as e:
        logger.warning(f"_ensure_columns failed: {e}")


def _get_user(request: Request) -> dict:
    user = getattr(getattr(request, "state", None), "user", None)
    if not user:
        raise HTTPException(401, "Not authenticated")
    return user


def _check_project(user: dict, slug: str, role: str = "viewer") -> dict:
    from app.auth import check_project_permission
    perm = check_project_permission(user, slug, required_role=role)
    if not perm:
        raise HTTPException(403, "Access denied")
    return perm


def _envflag(name: str, default: str = "true") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


def smtp_configured() -> bool:
    return bool(os.environ.get("SMTP_HOST")) and bool(os.environ.get("SMTP_FROM"))


def slack_configured() -> bool:
    return bool(os.environ.get("SLACK_WEBHOOK_URL"))


# ---------------------------------------------------------------------------
# Content builder
# ---------------------------------------------------------------------------

def _project_row(slug: str) -> Optional[dict]:
    try:
        with _engine().connect() as conn:
            r = conn.execute(text(
                "SELECT slug, name, digest_enabled, digest_email_to, "
                "  digest_slack_enabled, digest_time_utc, last_digest_sent_at, "
                "  last_digest_error, daily_cost_cap_usd "
                "FROM public.dash_projects WHERE slug = :s"
            ), {"s": slug}).fetchone()
        if not r:
            return None
        return {
            "slug": r[0], "name": r[1],
            "digest_enabled": bool(r[2]),
            "digest_email_to": r[3] or "",
            "digest_slack_enabled": bool(r[4]),
            "digest_time_utc": r[5] or "08:00",
            "last_digest_sent_at": r[6].isoformat() if r[6] else None,
            "last_digest_error": r[7],
            "daily_cost_cap_usd": float(r[8]) if r[8] is not None else None,
        }
    except Exception as e:
        logger.warning(f"_project_row({slug}): {e}")
        return None


def _gather_today(slug: str) -> dict:
    """Collect today's metrics for one project. Each query is best-effort."""
    out: dict[str, Any] = {
        "cycle_runs": 0,
        "cycle_success_rate": 0.0,
        "top_hypotheses": [],
        "drift_by_severity": {},
        "cost_usd": 0.0,
        "cost_cap_usd": None,
        "new_memories": [],
        "self_learn_summary": "",
    }
    eng = _engine()

    # cycle runs + success rate
    try:
        with eng.connect() as conn:
            row = conn.execute(text(
                "SELECT COUNT(*), "
                "  SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END), "
                "  COALESCE(SUM(cost_usd),0) "
                "FROM public.dash_self_learning_runs "
                "WHERE project_slug = :s "
                "  AND started_at >= NOW() - INTERVAL '1 day'"
            ), {"s": slug}).fetchone()
            if row:
                tot = int(row[0] or 0)
                ok = int(row[1] or 0)
                out["cycle_runs"] = tot
                out["cycle_success_rate"] = (ok / tot) if tot else 0.0
                out["cost_usd"] = float(row[2] or 0)
    except Exception as e:
        logger.debug(f"digest cycle stats: {e}")

    # top 3 hypotheses promoted to central in last day
    try:
        with eng.connect() as conn:
            rows = conn.execute(text(
                "SELECT statement, hypothesis_type, confidence "
                "FROM public.dash_hypotheses "
                "WHERE project_slug = :s "
                "  AND verification_status = 'verified' "
                "  AND promoted_to_central = TRUE "
                "  AND created_at >= NOW() - INTERVAL '1 day' "
                "ORDER BY confidence DESC LIMIT 3"
            ), {"s": slug}).fetchall()
            out["top_hypotheses"] = [{
                "statement": (r[0] or "")[:240],
                "type": r[1], "confidence": float(r[2] or 0),
            } for r in rows]
    except Exception as e:
        logger.debug(f"digest top hyp: {e}")

    # drift events by severity (last day, open)
    try:
        with eng.connect() as conn:
            rows = conn.execute(text(
                "SELECT severity, COUNT(*) FROM public.dash_drift_events "
                "WHERE project_slug = :s "
                "  AND detected_at >= NOW() - INTERVAL '1 day' "
                "GROUP BY severity"
            ), {"s": slug}).fetchall()
            out["drift_by_severity"] = {r[0]: int(r[1]) for r in rows}
    except Exception as e:
        logger.debug(f"digest drift: {e}")

    # cost cap
    try:
        with eng.connect() as conn:
            r = conn.execute(text(
                "SELECT daily_cost_cap_usd FROM public.dash_projects WHERE slug=:s"
            ), {"s": slug}).fetchone()
            if r and r[0] is not None:
                out["cost_cap_usd"] = float(r[0])
    except Exception:
        pass

    # top 5 new memories (auto_learned or episodic)
    try:
        with eng.connect() as conn:
            rows = conn.execute(text(
                "SELECT content, source FROM public.dash_memories "
                "WHERE project_slug = :s "
                "  AND source IN ('auto_learned', 'episodic') "
                "  AND created_at >= NOW() - INTERVAL '1 day' "
                "ORDER BY created_at DESC LIMIT 5"
            ), {"s": slug}).fetchall()
            out["new_memories"] = [{
                "content": (r[0] or "")[:200], "source": r[1],
            } for r in rows]
    except Exception as e:
        logger.debug(f"digest memories: {e}")

    # latest self-learn summary text
    try:
        with eng.connect() as conn:
            r = conn.execute(text(
                "SELECT summary FROM public.dash_digests "
                "WHERE project_slug = :s "
                "  AND created_at >= NOW() - INTERVAL '1 day' "
                "ORDER BY created_at DESC LIMIT 1"
            ), {"s": slug}).fetchone()
            if r and r[0]:
                out["self_learn_summary"] = (r[0] or "")[:600]
    except Exception:
        pass

    return out


def build_digest_content(slug: str) -> dict:
    """Build markdown + plain text + slack blocks for one project."""
    proj = _project_row(slug) or {"slug": slug, "name": slug}
    data = _gather_today(slug)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    name = proj.get("name") or slug

    # --- Markdown body
    md = [f"# Daily Digest — {name} ({today} UTC)\n"]
    md.append(
        f"**Cycles:** {data['cycle_runs']} runs · "
        f"success rate {data['cycle_success_rate']*100:.0f}%\n"
    )
    cap = data.get("cost_cap_usd")
    cap_str = f" / cap ${cap:.2f}" if cap is not None else ""
    md.append(f"**Cost:** ${data['cost_usd']:.3f}{cap_str}\n")
    drift = data["drift_by_severity"]
    if drift:
        drift_line = ", ".join(f"{k}={v}" for k, v in drift.items())
        md.append(f"**Drift events:** {drift_line}\n")
    else:
        md.append("**Drift events:** none\n")

    if data["top_hypotheses"]:
        md.append("\n## Top promoted hypotheses\n")
        for h in data["top_hypotheses"]:
            md.append(
                f"- _{h['type']}_ (conf {h['confidence']:.2f}) — {h['statement']}"
            )
    if data["new_memories"]:
        md.append("\n## New memories\n")
        for m in data["new_memories"]:
            md.append(f"- ({m['source']}) {m['content']}")
    if data["self_learn_summary"]:
        md.append("\n## Self-learning summary\n")
        md.append(data["self_learn_summary"])
    markdown_body = "\n".join(md)

    # --- Plain text fallback
    plain_lines = [
        f"Daily Digest — {name} ({today} UTC)",
        "",
        f"Cycles: {data['cycle_runs']} runs, "
        f"success rate {data['cycle_success_rate']*100:.0f}%",
        f"Cost: ${data['cost_usd']:.3f}{cap_str}",
    ]
    if drift:
        plain_lines.append("Drift: " + ", ".join(f"{k}={v}" for k, v in drift.items()))
    if data["top_hypotheses"]:
        plain_lines.append("")
        plain_lines.append("Top promoted hypotheses:")
        for h in data["top_hypotheses"]:
            plain_lines.append(
                f"  * [{h['type']}] (conf {h['confidence']:.2f}) {h['statement']}"
            )
    if data["new_memories"]:
        plain_lines.append("")
        plain_lines.append("New memories:")
        for m in data["new_memories"]:
            plain_lines.append(f"  * ({m['source']}) {m['content']}")
    if data["self_learn_summary"]:
        plain_lines.append("")
        plain_lines.append("Self-learning summary:")
        plain_lines.append(data["self_learn_summary"])
    plain_body = "\n".join(plain_lines)

    # --- Slack Block Kit
    blocks: list[dict] = [
        {"type": "header",
         "text": {"type": "plain_text",
                  "text": f"Daily Digest — {name}"}},
        {"type": "section",
         "fields": [
             {"type": "mrkdwn",
              "text": f"*Cycles*\n{data['cycle_runs']} runs "
                      f"({data['cycle_success_rate']*100:.0f}% ok)"},
             {"type": "mrkdwn",
              "text": f"*Cost*\n${data['cost_usd']:.3f}{cap_str}"},
         ]},
    ]
    if drift:
        drift_text = ", ".join(f"{k}={v}" for k, v in drift.items())
        blocks.append({"type": "section",
                       "text": {"type": "mrkdwn",
                                "text": f"*Drift events:* {drift_text}"}})
    if data["top_hypotheses"]:
        h_lines = "\n".join(
            f"• _{h['type']}_ ({h['confidence']:.2f}) {h['statement']}"
            for h in data["top_hypotheses"]
        )
        blocks.append({"type": "section",
                       "text": {"type": "mrkdwn",
                                "text": f"*Top promoted hypotheses*\n{h_lines}"}})
    if data["new_memories"]:
        m_lines = "\n".join(
            f"• ({m['source']}) {m['content']}" for m in data["new_memories"]
        )
        blocks.append({"type": "section",
                       "text": {"type": "mrkdwn",
                                "text": f"*New memories*\n{m_lines}"}})
    if data["self_learn_summary"]:
        blocks.append({"type": "section",
                       "text": {"type": "mrkdwn",
                                "text": f"*Self-learning summary*\n"
                                        f"{data['self_learn_summary']}"}})
    blocks.append({"type": "context",
                   "elements": [{"type": "mrkdwn",
                                 "text": f"Project `{slug}` · {today} UTC"}]})

    return {
        "subject": f"[Dash] Daily digest — {name} {today}",
        "markdown": markdown_body,
        "plain": plain_body,
        "blocks": blocks,
        "data": data,
        "project": proj,
    }


# ---------------------------------------------------------------------------
# Senders
# ---------------------------------------------------------------------------

def send_email(recipients: list[str], subject: str, plain: str, markdown: str) -> None:
    """Raises on failure. Multi-recipient via list."""
    host = os.environ["SMTP_HOST"]
    port = int(os.environ.get("SMTP_PORT", "587") or 587)
    user = os.environ.get("SMTP_USER", "")
    pwd = os.environ.get("SMTP_PASS", "")
    sender = os.environ["SMTP_FROM"]
    use_tls = _envflag("SMTP_TLS", "true")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(plain, "plain", "utf-8"))
    # naive markdown→html: keep markdown as plain pre-block for clients
    html = (
        "<html><body><pre style='font-family:monospace;font-size:13px;'>"
        + markdown.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        + "</pre></body></html>"
    )
    msg.attach(MIMEText(html, "html", "utf-8"))

    if port == 465:
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL(host, port, context=ctx, timeout=30) as srv:
            if user:
                srv.login(user, pwd)
            srv.sendmail(sender, recipients, msg.as_string())
    else:
        with smtplib.SMTP(host, port, timeout=30) as srv:
            srv.ehlo()
            if use_tls:
                srv.starttls(context=ssl.create_default_context())
                srv.ehlo()
            if user:
                srv.login(user, pwd)
            srv.sendmail(sender, recipients, msg.as_string())


def send_slack(blocks: list[dict]) -> None:
    """Raises on non-2xx. Skips silently if webhook missing — caller checks."""
    webhook = os.environ.get("SLACK_WEBHOOK_URL")
    if not webhook:
        raise RuntimeError("slack_not_configured")
    import requests
    r = requests.post(webhook, json={"blocks": blocks}, timeout=15)
    if r.status_code not in (200, 204):
        raise RuntimeError(f"slack http {r.status_code}: {r.text[:200]}")


def send_digest(slug: str) -> dict:
    """Build + send to all configured channels for one project.

    Returns {ok, channels:[...], errors:[...]}. Never raises.
    """
    result: dict[str, Any] = {"ok": False, "channels": [], "errors": []}
    proj = _project_row(slug)
    if not proj:
        result["errors"].append("project_not_found")
        return result

    try:
        content = build_digest_content(slug)
    except Exception as e:
        logger.exception(f"build_digest_content({slug}) failed: {e}")
        result["errors"].append(f"build_failed: {e}")
        return result

    # Email
    email_to = (proj.get("digest_email_to") or "").strip()
    if email_to:
        if not smtp_configured():
            result["errors"].append("smtp_not_configured")
        else:
            recips = [e.strip() for e in email_to.split(",") if e.strip()]
            try:
                send_email(recips, content["subject"], content["plain"],
                           content["markdown"])
                result["channels"].append("email")
            except Exception as e:
                logger.warning(f"digest email failed for {slug}: {e}")
                result["errors"].append(f"email: {e}")

    # Slack
    if proj.get("digest_slack_enabled"):
        if not slack_configured():
            result["errors"].append("slack_not_configured")
        else:
            try:
                send_slack(content["blocks"])
                result["channels"].append("slack")
            except Exception as e:
                logger.warning(f"digest slack failed for {slug}: {e}")
                result["errors"].append(f"slack: {e}")

    # If user configured neither channel, surface that
    if not email_to and not proj.get("digest_slack_enabled"):
        result["errors"].append("no_channel_configured")

    # Persist last_sent / last_error
    try:
        with _write_eng().begin() as conn:
            if result["channels"]:
                conn.execute(text(
                    "UPDATE public.dash_projects SET "
                    "  last_digest_sent_at = NOW(), "
                    "  last_digest_error = :err "
                    "WHERE slug = :s"
                ), {"err": (("; ".join(result["errors"]))[:500]
                            if result["errors"] else None),
                    "s": slug})
            elif result["errors"]:
                conn.execute(text(
                    "UPDATE public.dash_projects SET "
                    "  last_digest_error = :err WHERE slug = :s"
                ), {"err": ("; ".join(result["errors"]))[:500], "s": slug})
        # audit log (best-effort)
        try:
            from app.auth import log_action
            log_action(None, "digest_sent", "project", slug,
                       f"channels={','.join(result['channels'])}; "
                       f"errors={','.join(result['errors'])}")
        except Exception:
            pass
    except Exception as e:
        logger.warning(f"digest persist for {slug}: {e}")

    result["ok"] = bool(result["channels"])
    return result


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------

_DIGEST_POLL_SECONDS = 5 * 60  # 5 min
_DIGEST_WINDOW_MIN = 2          # +/- 2 min match window
_digest_task: Optional[asyncio.Task] = None
_digest_state: dict[str, Any] = {
    "last_poll": None, "last_sent_count": 0,
    "last_error": None, "enabled": True,
}


def _due_projects(now_utc: datetime) -> list[str]:
    """Return project slugs whose digest_time_utc matches now within window
    and which haven't been sent in the last 23 hours."""
    hh = now_utc.hour
    mm = now_utc.minute
    # Build candidate HH:MM strings within the +/- window (minute granularity)
    candidates: set[str] = set()
    for delta in range(-_DIGEST_WINDOW_MIN, _DIGEST_WINDOW_MIN + 1):
        t = now_utc + timedelta(minutes=delta)
        candidates.add(t.strftime("%H:%M"))
    try:
        with _engine().connect() as conn:
            rows = conn.execute(text(
                "SELECT slug, digest_time_utc, last_digest_sent_at "
                "FROM public.dash_projects "
                "WHERE digest_enabled = TRUE"
            )).fetchall()
        out: list[str] = []
        for slug, t_utc, last_sent in rows:
            t_norm = (t_utc or "08:00").strip()
            # Accept 'HH:MM' only — reject malformed
            if len(t_norm) >= 4 and t_norm[:5] in candidates:
                # not sent in the last 23h
                if last_sent is not None:
                    age = now_utc - last_sent.astimezone(timezone.utc).replace(tzinfo=None) \
                        if last_sent.tzinfo is None else now_utc - last_sent.astimezone(timezone.utc)
                    if age.total_seconds() < 23 * 3600:
                        continue
                out.append(slug)
        return out
    except Exception as e:
        logger.warning(f"_due_projects: {e}")
        return []


async def _digest_loop() -> None:
    logger.info(f"digest scheduler starting (poll {_DIGEST_POLL_SECONDS}s)")
    # short startup grace
    try:
        await asyncio.sleep(30)
    except asyncio.CancelledError:
        return
    while True:
        if not _digest_state.get("enabled", True):
            try:
                await asyncio.sleep(_DIGEST_POLL_SECONDS)
            except asyncio.CancelledError:
                return
            continue
        try:
            now = datetime.utcnow().replace(tzinfo=None)
            slugs = await asyncio.to_thread(_due_projects, now)
            sent = 0
            for slug in slugs:
                try:
                    res = await asyncio.to_thread(send_digest, slug)
                    if res.get("ok"):
                        sent += 1
                except Exception as e:
                    logger.warning(f"digest send for {slug}: {e}")
            _digest_state["last_poll"] = datetime.utcnow().isoformat()
            _digest_state["last_sent_count"] = sent
            _digest_state["last_error"] = None
        except Exception as e:
            logger.exception(f"digest loop: {e}")
            _digest_state["last_error"] = str(e)[:300]
        try:
            await asyncio.sleep(_DIGEST_POLL_SECONDS)
        except asyncio.CancelledError:
            return


def start_digest_scheduler() -> Optional[asyncio.Task]:
    """Idempotent. Called from app startup."""
    global _digest_task
    if _digest_task is not None and not _digest_task.done():
        return _digest_task
    # disable in K8S to avoid multi-pod duplicate sends
    if os.environ.get("KUBERNETES_SERVICE_HOST") and not \
            os.environ.get("DIGEST_SCHEDULER_FORCE_INPROCESS"):
        logger.info("K8S detected — digest scheduler disabled (use CronJob)")
        _digest_state["enabled"] = False
        return None
    if os.environ.get("DIGEST_SCHEDULER_DISABLED", "").lower() in ("1", "true", "yes"):
        logger.info("digest scheduler disabled via env")
        _digest_state["enabled"] = False
        return None
    try:
        _ensure_columns()
        loop = asyncio.get_event_loop()
        _digest_task = loop.create_task(_digest_loop())
        logger.info("digest scheduler task started")
        return _digest_task
    except RuntimeError as e:
        logger.warning(f"start_digest_scheduler: {e}")
        return None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/api/projects/{slug}/digest/config")
def get_digest_config(slug: str, request: Request):
    user = _get_user(request)
    _check_project(user, slug, role="viewer")
    proj = _project_row(slug)
    if not proj:
        raise HTTPException(404, "project not found")
    return {
        "slug": slug,
        "enabled": proj["digest_enabled"],
        "email_to": proj["digest_email_to"],
        "slack_enabled": proj["digest_slack_enabled"],
        "time_utc": proj["digest_time_utc"],
        "last_sent_at": proj["last_digest_sent_at"],
        "last_error": proj["last_digest_error"],
        "smtp_configured": smtp_configured(),
        "slack_configured": slack_configured(),
        "scheduler_enabled": bool(_digest_state.get("enabled", True)),
    }


@router.post("/api/projects/{slug}/digest/config")
async def set_digest_config(slug: str, request: Request):
    user = _get_user(request)
    _check_project(user, slug, role="editor")
    try:
        body = await request.json()
    except Exception:
        body = {}
    enabled = bool(body.get("enabled", False))
    email_to = (body.get("email_to") or "").strip()[:1000] or None
    slack_enabled = bool(body.get("slack_enabled", False))
    time_utc = (body.get("time_utc") or "08:00").strip()
    # validate HH:MM
    try:
        datetime.strptime(time_utc, "%H:%M")
    except Exception:
        raise HTTPException(400, "time_utc must be HH:MM")

    try:
        with _write_eng().begin() as conn:
            conn.execute(text(
                "UPDATE public.dash_projects SET "
                "  digest_enabled = :en, "
                "  digest_email_to = :em, "
                "  digest_slack_enabled = :sl, "
                "  digest_time_utc = :tu "
                "WHERE slug = :s"
            ), {"en": enabled, "em": email_to, "sl": slack_enabled,
                "tu": time_utc, "s": slug})
    except Exception as e:
        logger.exception(f"digest config write {slug}: {e}")
        raise HTTPException(500, "config update failed")
    return get_digest_config(slug, request)


@router.post("/api/projects/{slug}/digest/test")
async def test_digest(slug: str, request: Request):
    user = _get_user(request)
    _check_project(user, slug, role="editor")
    res = await asyncio.to_thread(send_digest, slug)
    return res
