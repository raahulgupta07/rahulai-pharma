"""Slack digest for Dream Reflection nightly cycles.

Composes a compact, Slack-friendly summary from the latest `dash_dream_runs`
row plus surrounding deltas (new anti-patterns, facts invalidated via the
bi-temporal columns, skills promoted, cost). Posts to the same
`SLACK_LEARNING_WEBHOOK` env used by `dash/learning/digest.py`.

Fail-soft: if the webhook env is unset or any query fails, returns
`{posted: False, reason: "..."}` and never raises. Message capped at 3KB.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

_MAX_MSG_BYTES = 3 * 1024


def _engine():
    from db.session import get_sql_engine
    return get_sql_engine()


def _scalar(conn, sql: str, params: dict) -> int:
    try:
        from sqlalchemy import text
        v = conn.execute(text(sql), params).scalar()
        return int(v or 0)
    except Exception:
        return 0


def _gather(project_slug: str, hours: int) -> dict[str, Any]:
    """Pull counts for digest. All queries are best-effort fail-soft."""
    from sqlalchemy import text
    eng = _engine()
    out: dict[str, Any] = {
        "project_slug": project_slug,
        "hours": hours,
        "last_run": None,
        "findings": 0,
        "anti_patterns_new": 0,
        "facts_invalidated_brain": 0,
        "facts_invalidated_kg": 0,
        "skills_promoted": 0,
        "cost_usd": 0.0,
    }
    interval = f"{int(hours)} hours"
    try:
        with eng.connect() as conn:
            # Latest dream run
            try:
                row = conn.execute(text(
                    "SELECT id, started_at, finished_at, findings_count, "
                    "       cost_usd, status "
                    "  FROM public.dash_dream_runs "
                    " WHERE project_slug = :s "
                    "   AND started_at > now() - (:i)::interval "
                    " ORDER BY started_at DESC LIMIT 1"
                ), {"s": project_slug, "i": interval}).mappings().first()
                if row:
                    out["last_run"] = {
                        k: (v.isoformat() if hasattr(v, "isoformat") else v)
                        for k, v in dict(row).items()
                    }
                    out["findings"] = int(row.get("findings_count") or 0)
                    out["cost_usd"] = float(row.get("cost_usd") or 0)
            except Exception:
                logger.debug("dream_slack: dream_runs query failed", exc_info=True)

            out["anti_patterns_new"] = _scalar(conn,
                "SELECT COUNT(*) FROM public.dash_anti_patterns "
                "WHERE project_slug = :s "
                "  AND created_at > now() - (:i)::interval",
                {"s": project_slug, "i": interval},
            )

            # Bi-temporal invalidations (try both schemas)
            for sch in ("dash", "public"):
                try:
                    out["facts_invalidated_brain"] += _scalar(conn,
                        f"SELECT COUNT(*) FROM {sch}.dash_company_brain "
                        f"WHERE (project_slug = :s OR project_slug IS NULL) "
                        f"  AND expired_at IS NOT NULL "
                        f"  AND expired_at > now() - (:i)::interval",
                        {"s": project_slug, "i": interval},
                    )
                except Exception:
                    pass
                try:
                    out["facts_invalidated_kg"] += _scalar(conn,
                        f"SELECT COUNT(*) FROM {sch}.dash_knowledge_triples "
                        f"WHERE (project_slug = :s OR project_slug IS NULL) "
                        f"  AND expired_at IS NOT NULL "
                        f"  AND expired_at > now() - (:i)::interval",
                        {"s": project_slug, "i": interval},
                    )
                except Exception:
                    pass

            out["skills_promoted"] = _scalar(conn,
                "SELECT COUNT(*) FROM public.dash_skill_library "
                "WHERE project_slug = :s "
                "  AND created_at > now() - (:i)::interval",
                {"s": project_slug, "i": interval},
            )
    except Exception:
        logger.exception("dream_slack: gather failed")
    return out


def _format(d: dict[str, Any]) -> str:
    proj = d.get("project_slug") or "central"
    hrs = d.get("hours", 24)
    parts = [
        f":crystal_ball: *Dream Reflection digest — {proj}* (last {hrs}h)",
        f"• Findings: *{d['findings']}*",
        f"• New anti-patterns: *{d['anti_patterns_new']}*",
        f"• Facts invalidated (brain): *{d['facts_invalidated_brain']}*",
        f"• Facts invalidated (KG): *{d['facts_invalidated_kg']}*",
        f"• Skills promoted: *{d['skills_promoted']}*",
        f"• Cost: *${d['cost_usd']:.3f}*",
    ]
    lr = d.get("last_run")
    if lr:
        parts.append(
            f"• Last run: #{lr.get('id')} status=`{lr.get('status')}` "
            f"finished `{lr.get('finished_at') or 'running'}`"
        )
    msg = "\n".join(parts)
    if len(msg.encode("utf-8")) > _MAX_MSG_BYTES:
        msg = msg.encode("utf-8")[:_MAX_MSG_BYTES].decode("utf-8", "ignore")
    return msg


def send_digest(project_slug: str, hours: int = 24) -> dict:
    """Compose + post the Slack digest for one project.

    Returns {posted: bool, reason: str|None, message: str|None}.
    Never raises.
    """
    webhook = os.environ.get("SLACK_LEARNING_WEBHOOK")
    if not webhook:
        return {"posted": False, "reason": "no_webhook"}
    try:
        data = _gather(project_slug, hours)
        msg = _format(data)
        try:
            import requests  # type: ignore
            r = requests.post(webhook, json={"text": msg}, timeout=10)
            ok = r.status_code in (200, 204)
            return {
                "posted": bool(ok),
                "reason": None if ok else f"http_{r.status_code}",
                "message": msg,
            }
        except Exception as e:
            try:
                import httpx  # type: ignore
                r = httpx.post(webhook, json={"text": msg}, timeout=10)
                ok = r.status_code in (200, 204)
                return {
                    "posted": bool(ok),
                    "reason": None if ok else f"http_{r.status_code}",
                    "message": msg,
                }
            except Exception:
                logger.debug("dream_slack: post failed", exc_info=True)
                return {"posted": False, "reason": f"post_error:{e}"}
    except Exception as e:
        logger.exception("dream_slack: send_digest failed")
        return {"posted": False, "reason": f"error:{e}"}


def send_daily_digest_all() -> dict:
    """Iterate active projects with `feature_config.tools.dream_reflection`
    enabled, send digest each, return summary.
    """
    from sqlalchemy import text
    summary = {"projects": 0, "posted": 0, "skipped": 0, "errors": 0,
               "results": []}
    try:
        eng = _engine()
        with eng.connect() as conn:
            rows = conn.execute(text(
                "SELECT slug FROM public.dash_projects "
                "WHERE COALESCE(feature_config -> 'tools' ->> 'dream_reflection', "
                "               'true') = 'true'"
            )).fetchall()
        slugs = [r[0] for r in rows if r and r[0]]
        summary["projects"] = len(slugs)
        for s in slugs:
            try:
                res = send_digest(s)
                summary["results"].append({"slug": s, **res})
                if res.get("posted"):
                    summary["posted"] += 1
                else:
                    summary["skipped"] += 1
            except Exception as e:
                summary["errors"] += 1
                summary["results"].append({"slug": s, "error": str(e)})
    except Exception:
        logger.exception("dream_slack: send_daily_digest_all failed")
    return summary
