"""Dash-OS Phase 2E — Agent-callable scheduling @tool wrappers.

Lets agents self-schedule recurring tasks. Behind EXPERIMENTAL_AGI=1;
otherwise tools return informational message, no DB writes.

Per-project cap: 20 schedules. Per-agent cap: 5 per project. Dedup on
(project_slug, created_by_agent, name).

Daemon (dash/cron/agent_schedule_runner.py) polls every 30s and executes
due schedules via internal HTTP call to chat endpoint.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import re
import secrets
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

PER_PROJECT_CAP = int(os.getenv("AGENT_SCHEDULE_PER_PROJECT_CAP", "20"))
PER_AGENT_CAP = int(os.getenv("AGENT_SCHEDULE_PER_AGENT_CAP", "5"))
CRON_REGEX = re.compile(r"^[0-9*/,\-]+( [0-9*/,\-]+){4}$")


def _enabled() -> bool:
    return os.getenv("EXPERIMENTAL_AGI") == "1"


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


def _ctx() -> Dict[str, Any]:
    try:
        from dash.agentic.hooks import (
            current_project_slug, current_user_id, current_agent_name,
        )
        return {
            "project_slug": current_project_slug.get(),
            "user_id": current_user_id.get(),
            "agent_name": current_agent_name.get(),
        }
    except Exception:
        return {"project_slug": None, "user_id": None, "agent_name": None}


def _validate_cron(expr: str) -> bool:
    try:
        from croniter import croniter
        return croniter.is_valid(expr)
    except Exception:
        return bool(CRON_REGEX.match(expr.strip()))


def _compute_next(kind: str, cron_expr: Optional[str], interval_seconds: Optional[int],
                  start: Optional[dt.datetime] = None) -> Optional[dt.datetime]:
    base = start or dt.datetime.utcnow()
    if kind == "cron" and cron_expr:
        try:
            from croniter import croniter
            return croniter(cron_expr, base).get_next(dt.datetime)
        except Exception:
            # fallback: +1h
            return base + dt.timedelta(hours=1)
    if kind == "interval" and interval_seconds:
        return base + dt.timedelta(seconds=interval_seconds)
    if kind == "once":
        return start  # caller provides
    return None


def _try_tool(fn):
    try:
        from agno.tools import tool
        return tool(fn)
    except Exception:
        return fn


def _check_caps(project_slug: Optional[str], created_by_agent: Optional[str]) -> Optional[str]:
    eng = _get_engine()
    if eng is None:
        return None
    try:
        from sqlalchemy import text
        with eng.connect() as conn:
            proj_count = conn.execute(
                text(
                    "SELECT COUNT(*) FROM dash.dash_agent_schedules "
                    "WHERE (:ps IS NULL OR project_slug = :ps) AND enabled=true"
                ),
                {"ps": project_slug},
            ).scalar() or 0
            if proj_count >= PER_PROJECT_CAP:
                return f"per_project_cap_reached:{PER_PROJECT_CAP}"
            if created_by_agent:
                agent_count = conn.execute(
                    text(
                        "SELECT COUNT(*) FROM dash.dash_agent_schedules "
                        "WHERE project_slug IS NOT DISTINCT FROM :ps "
                        "  AND created_by_agent = :ag AND enabled=true"
                    ),
                    {"ps": project_slug, "ag": created_by_agent},
                ).scalar() or 0
                if agent_count >= PER_AGENT_CAP:
                    return f"per_agent_cap_reached:{PER_AGENT_CAP}"
    except Exception as e:
        logger.warning("cap check failed: %s", e)
    return None


def _dedup_lookup(project_slug: Optional[str], created_by_agent: Optional[str], name: str) -> Optional[str]:
    eng = _get_engine()
    if eng is None:
        return None
    try:
        from sqlalchemy import text
        with eng.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT id FROM dash.dash_agent_schedules "
                    "WHERE project_slug IS NOT DISTINCT FROM :ps "
                    "  AND created_by_agent IS NOT DISTINCT FROM :ag "
                    "  AND name = :nm"
                ),
                {"ps": project_slug, "ag": created_by_agent, "nm": name},
            ).first()
        return row[0] if row else None
    except Exception:
        return None


def _insert(
    kind: str, name: str, prompt: str, agent_target: str = "leader",
    cron_expr: Optional[str] = None, interval_seconds: Optional[int] = None,
    next_run_at: Optional[dt.datetime] = None, max_runs: Optional[int] = None,
    description: Optional[str] = None,
) -> Dict[str, Any]:
    if not _enabled():
        return {
            "ok": False, "reason": "scheduler_disabled",
            "message": "Set EXPERIMENTAL_AGI=1 to enable agent scheduling",
        }
    ctx = _ctx()
    cap_err = _check_caps(ctx["project_slug"], ctx["agent_name"])
    if cap_err:
        return {"ok": False, "reason": cap_err}
    existing = _dedup_lookup(ctx["project_slug"], ctx["agent_name"], name)
    if existing:
        return {"ok": True, "schedule_id": existing, "dedup": True}

    sid = "sch_" + secrets.token_hex(4)
    eng = _get_engine()
    if eng is None:
        return {"ok": False, "reason": "db_unavailable"}
    try:
        from sqlalchemy import text
        with eng.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO dash.dash_agent_schedules
                      (id, project_slug, created_by, created_by_agent, name, description,
                       schedule_kind, cron_expr, interval_seconds, next_run_at, prompt,
                       agent_target, max_runs)
                    VALUES (:id, :ps, :cb, :ag, :nm, :ds, :kd, :cron, :iv, :nr, :pt, :at, :mr)
                    """
                ),
                {
                    "id": sid, "ps": ctx["project_slug"],
                    "cb": ctx["user_id"] or 0, "ag": ctx["agent_name"],
                    "nm": name, "ds": description, "kd": kind,
                    "cron": cron_expr, "iv": interval_seconds, "nr": next_run_at,
                    "pt": prompt, "at": agent_target, "mr": max_runs,
                },
            )
    except Exception as e:
        return {"ok": False, "reason": "insert_failed", "error": str(e)}
    return {
        "ok": True, "schedule_id": sid,
        "next_run_at": next_run_at.isoformat() if next_run_at else None,
    }


def _schedule_recurring(
    name: str, cron: str, prompt: str,
    agent_target: str = "leader", max_runs: Optional[int] = None,
    description: Optional[str] = None,
) -> Dict[str, Any]:
    if not _validate_cron(cron):
        return {"ok": False, "error": f"invalid cron: {cron}"}
    nr = _compute_next("cron", cron, None)
    return _insert(
        "cron", name, prompt, agent_target=agent_target,
        cron_expr=cron, next_run_at=nr, max_runs=max_runs, description=description,
    )


def _schedule_interval(
    name: str, every_seconds: int, prompt: str,
    agent_target: str = "leader", max_runs: Optional[int] = None,
    description: Optional[str] = None,
) -> Dict[str, Any]:
    if every_seconds < 60 or every_seconds > 604800:
        return {"ok": False, "error": "every_seconds must be 60..604800"}
    nr = dt.datetime.utcnow() + dt.timedelta(seconds=every_seconds)
    return _insert(
        "interval", name, prompt, agent_target=agent_target,
        interval_seconds=every_seconds, next_run_at=nr,
        max_runs=max_runs, description=description,
    )


def _schedule_once(
    name: str, at: str, prompt: str,
    agent_target: str = "leader", description: Optional[str] = None,
) -> Dict[str, Any]:
    try:
        when = dt.datetime.fromisoformat(at.replace("Z", "+00:00"))
        if when.tzinfo:
            when = when.replace(tzinfo=None)
    except Exception as e:
        return {"ok": False, "error": f"invalid iso datetime: {e}"}
    if when < dt.datetime.utcnow() + dt.timedelta(seconds=60):
        return {"ok": False, "error": "at must be >= now+60s"}
    return _insert(
        "once", name, prompt, agent_target=agent_target,
        next_run_at=when, max_runs=1, description=description,
    )


def _list_schedules(
    project_slug: Optional[str] = None, enabled_only: bool = True, limit: int = 50,
) -> Dict[str, Any]:
    eng = _get_engine()
    if eng is None:
        return {"ok": False, "schedules": []}
    try:
        from sqlalchemy import text
        with eng.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT id, project_slug, name, schedule_kind, cron_expr,
                           interval_seconds, next_run_at, agent_target, enabled,
                           run_count, last_run_at, last_run_result
                    FROM dash.dash_agent_schedules
                    WHERE (:ps IS NULL OR project_slug = :ps)
                      AND (:eo = false OR enabled = true)
                    ORDER BY next_run_at NULLS LAST LIMIT :lim
                    """
                ),
                {"ps": project_slug, "eo": enabled_only, "lim": limit},
            ).mappings().all()
        return {"ok": True, "schedules": [dict(r) for r in rows]}
    except Exception as e:
        return {"ok": False, "error": str(e), "schedules": []}


def _toggle(sid: str, enabled: bool) -> Dict[str, Any]:
    eng = _get_engine()
    if eng is None:
        return {"ok": False, "error": "db unavailable"}
    try:
        from sqlalchemy import text
        with eng.begin() as conn:
            r = conn.execute(
                text(
                    "UPDATE dash.dash_agent_schedules SET enabled=:e, updated_at=now() "
                    "WHERE id=:id"
                ),
                {"e": enabled, "id": sid},
            )
        return {"ok": r.rowcount > 0, "schedule_id": sid, "enabled": enabled}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _enable_schedule(schedule_id: str) -> Dict[str, Any]:
    return _toggle(schedule_id, True)


def _disable_schedule(schedule_id: str) -> Dict[str, Any]:
    return _toggle(schedule_id, False)


def _delete_schedule(schedule_id: str) -> Dict[str, Any]:
    eng = _get_engine()
    if eng is None:
        return {"ok": False, "error": "db unavailable"}
    try:
        from sqlalchemy import text
        with eng.begin() as conn:
            r = conn.execute(
                text("DELETE FROM dash.dash_agent_schedules WHERE id=:id"),
                {"id": schedule_id},
            )
        return {"ok": r.rowcount > 0, "deleted": schedule_id}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _run_schedule_now(schedule_id: str) -> Dict[str, Any]:
    eng = _get_engine()
    if eng is None:
        return {"ok": False, "error": "db unavailable"}
    try:
        from sqlalchemy import text
        with eng.begin() as conn:
            r = conn.execute(
                text(
                    "UPDATE dash.dash_agent_schedules SET next_run_at=now(), updated_at=now() "
                    "WHERE id=:id AND enabled=true"
                ),
                {"id": schedule_id},
            )
        return {"ok": r.rowcount > 0, "queued": schedule_id}
    except Exception as e:
        return {"ok": False, "error": str(e)}


schedule_recurring = _try_tool(_schedule_recurring)
schedule_interval = _try_tool(_schedule_interval)
schedule_once = _try_tool(_schedule_once)
list_schedules = _try_tool(_list_schedules)
enable_schedule = _try_tool(_enable_schedule)
disable_schedule = _try_tool(_disable_schedule)
delete_schedule = _try_tool(_delete_schedule)
run_schedule_now = _try_tool(_run_schedule_now)
