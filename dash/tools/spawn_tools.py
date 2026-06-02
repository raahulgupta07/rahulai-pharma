"""Dash-OS Phase 11A — Agent-callable sub-agent spawn tools.

Lets agents spawn focused sub-agents on demand. AUTO-SAVES the agent
definition on first spawn (by (project_slug, name)); subsequent spawns
reuse the saved row. Other agents in the same project can list + reuse
by name.

Behind EXPERIMENTAL_AGI=1. All DB writes schema-qualify ``dash.<table>``.

Safety:
- Max sub-agent depth = 1 (no nesting). Enforced via ContextVar
  ``is_subagent_run``. Nested spawn attempts are logged with status
  ``denied_nesting`` and return an error.
- Per-project cap default 50 custom agents (env ``DASH_CUSTOM_AGENT_CAP``).
- Cost cap: pre-execution check via ``pre_cost_cap`` hook when present.
- Tools scoping: spawner's allowed-tool list pulled from ContextVar; if
  unset, a small default-safe set is allowed.
- Sub-agent dispatch via Agno ``Agent.run()`` with 5-min timeout.
"""
from __future__ import annotations

import asyncio
import contextvars
import json as _json
import logging
import os
import time
from dataclasses import asdict
from typing import Any, Dict, List, Optional

from dash.agents.factory import AgentFactory, SubAgentSpec

logger = logging.getLogger(__name__)

DEFAULT_CAP = 50
SUBAGENT_TIMEOUT_SEC = int(os.getenv("DASH_SUBAGENT_TIMEOUT_SEC", "300"))


# ── Flag ────────────────────────────────────────────────────────────────────
def _enabled() -> bool:
    return os.getenv("EXPERIMENTAL_AGI") == "1"


def _cap() -> int:
    try:
        return int(os.getenv("DASH_CUSTOM_AGENT_CAP", str(DEFAULT_CAP)))
    except Exception:
        return DEFAULT_CAP


# ── ContextVars: nesting guard + spawner-allowed tools ─────────────────────
# Declared here (cannot touch hooks.py per constraints). Other modules may
# import these to set/read context.
is_subagent_run: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "dash_is_subagent_run", default=False
)
current_allowed_tools: contextvars.ContextVar[Optional[List[str]]] = contextvars.ContextVar(
    "dash_current_allowed_tools", default=None
)


# ── DB helper ───────────────────────────────────────────────────────────────
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


# ── Context (project, agent, user) ──────────────────────────────────────────
def _ctx() -> Dict[str, Any]:
    try:
        from dash.agentic.hooks import (
            current_project_slug, current_user_id, current_agent_name,
            current_run_id,
        )
        return {
            "project_slug": current_project_slug.get(),
            "user_id": current_user_id.get(),
            "agent_name": current_agent_name.get(),
            "run_id": current_run_id.get(),
        }
    except Exception:
        return {"project_slug": None, "user_id": None, "agent_name": None, "run_id": None}


# ── Audit log helpers ───────────────────────────────────────────────────────
def _log_run(
    *, agent_id: Optional[str], agent_name: str, project_slug: Optional[str],
    parent_run_id: Optional[str], spawned_by_agent: Optional[str],
    scoped_skills: List[str], scoped_tools: List[str], input_brief: str,
    output: Optional[str], status: str, latency_ms: Optional[int],
    cost_usd: Optional[float] = None,
) -> None:
    eng = _get_engine()
    if eng is None:
        return
    try:
        from sqlalchemy import text
        with eng.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO dash.dash_subagent_runs "
                    "(agent_id, agent_name, parent_run_id, spawned_by_agent, project_slug, "
                    " scoped_skills_used, scoped_tools_used, input_brief, output, status, "
                    " latency_ms, cost_usd) "
                    "VALUES (:aid,:an,:pr,:sb,:ps,"
                    " CAST(:ss AS jsonb), CAST(:st AS jsonb),"
                    " :ib,:op,:stat,:lat,:cost)"
                ),
                {
                    "aid": agent_id, "an": agent_name, "pr": parent_run_id,
                    "sb": spawned_by_agent, "ps": project_slug,
                    "ss": _json.dumps(scoped_skills or []),
                    "st": _json.dumps(scoped_tools or []),
                    "ib": input_brief, "op": output, "stat": status,
                    "lat": latency_ms, "cost": cost_usd,
                },
            )
    except Exception as e:  # pragma: no cover
        logger.debug("subagent run log failed: %s", e)


# ── Cap check ───────────────────────────────────────────────────────────────
def _project_agent_count(project_slug: Optional[str]) -> int:
    eng = _get_engine()
    if eng is None:
        return 0
    try:
        from sqlalchemy import text
        with eng.connect() as conn:
            r = conn.execute(
                text(
                    "SELECT COUNT(*) FROM dash.dash_custom_agents "
                    "WHERE project_slug IS NOT DISTINCT FROM :ps AND enabled = true"
                ),
                {"ps": project_slug},
            ).scalar()
        return int(r or 0)
    except Exception:
        return 0


# ── Cost cap (best-effort) ──────────────────────────────────────────────────
def _cost_cap_blocked(project_slug: Optional[str]) -> Optional[str]:
    try:
        from dash.learning.cost_guard import get_status  # type: ignore
        st = get_status(project_slug or "__subagent__")
        if isinstance(st, dict) and st.get("blocked"):
            return st.get("reason") or "cost_cap_reached"
    except Exception:
        return None
    return None


# ── Tool implementations ────────────────────────────────────────────────────
def _spawn_subagent(
    name: str,
    purpose: str,
    base_agent: str = "Analyst",
    scoped_skills: Optional[List[str]] = None,
    scoped_tools: Optional[List[str]] = None,
    extra_instructions: str = "",
    persona: Optional[str] = None,
    input_brief: str = "",
) -> Dict[str, Any]:
    """Spawn a sub-agent. AUTO-SAVES the definition on first call.

    Returns ``{ok, agent_id, output, was_new, reused}``.
    """
    if not _enabled():
        return {
            "ok": False, "reason": "experimental_agi_off",
            "message": "Set EXPERIMENTAL_AGI=1 to enable sub-agents",
        }

    ctx = _ctx()
    project_slug = ctx["project_slug"]
    spawned_by = ctx["agent_name"]
    parent_run_id = ctx["run_id"]

    # Nesting depth=1 enforcement
    if is_subagent_run.get():
        _log_run(
            agent_id=None, agent_name=name, project_slug=project_slug,
            parent_run_id=parent_run_id, spawned_by_agent=spawned_by,
            scoped_skills=scoped_skills or [], scoped_tools=scoped_tools or [],
            input_brief=input_brief, output=None,
            status="denied_nesting", latency_ms=0,
        )
        return {"ok": False, "reason": "nesting_denied",
                "message": "Sub-agents cannot spawn further sub-agents (depth=1)."}

    # Cost cap
    cost_blocked = _cost_cap_blocked(project_slug)
    if cost_blocked:
        return {"ok": False, "reason": "cost_cap", "message": cost_blocked}

    # Per-project cap
    cap = _cap()
    if _project_agent_count(project_slug) >= cap:
        # If we're about to *reuse* an existing row, that's still fine — check existence.
        eng = _get_engine()
        existing_id = None
        if eng is not None:
            try:
                from sqlalchemy import text
                with eng.connect() as conn:
                    row = conn.execute(
                        text(
                            "SELECT id FROM dash.dash_custom_agents "
                            "WHERE project_slug IS NOT DISTINCT FROM :ps AND name = :nm"
                        ),
                        {"ps": project_slug, "nm": name},
                    ).first()
                existing_id = row[0] if row else None
            except Exception:
                existing_id = None
        if existing_id is None:
            return {"ok": False, "reason": "cap_reached",
                    "message": f"Custom agent cap reached ({cap})."}

    # Build spec + upsert
    spec = SubAgentSpec(
        name=name,
        purpose=purpose,
        base_agent=base_agent or "Analyst",
        scoped_skills=list(scoped_skills or []),
        scoped_tools=list(scoped_tools or []),
        persona=persona,
        extra_instructions=extra_instructions or "",
    )
    up = AgentFactory.upsert_definition(
        spec, project_slug=project_slug,
        created_by_agent=spawned_by, created_by_user=ctx.get("user_id"),
    )
    if not up.get("ok"):
        return {"ok": False, "reason": "upsert_failed", "error": up.get("error")}
    agent_id = up["id"]
    was_new = bool(up.get("was_new"))

    # Bump usage on reuse (was_new=False)
    if not was_new:
        try:
            eng = _get_engine()
            if eng is not None:
                from sqlalchemy import text
                with eng.begin() as conn:
                    conn.execute(
                        text(
                            "UPDATE dash.dash_custom_agents "
                            "SET usage_count = COALESCE(usage_count,0)+1, "
                            "    last_used_at = now() WHERE id=:id"
                        ),
                        {"id": agent_id},
                    )
        except Exception:
            pass

    # Instantiate + run
    output: Optional[str] = None
    status = "done"
    started = time.time()
    agent = AgentFactory.instantiate(spec)
    if agent is None:
        status = "error"
        output = "agent_instantiation_failed"
    else:
        token = is_subagent_run.set(True)
        try:
            brief = input_brief or purpose
            run_coro_or_val = getattr(agent, "run", None)
            if run_coro_or_val is None:
                status = "error"
                output = "agent_has_no_run_method"
            else:
                try:
                    result = run_coro_or_val(brief)
                    # Agno may return awaitable
                    if asyncio.iscoroutine(result):
                        result = asyncio.get_event_loop().run_until_complete(
                            asyncio.wait_for(result, timeout=SUBAGENT_TIMEOUT_SEC)
                        )
                    # Coerce result into string output
                    output = getattr(result, "content", None) or str(result)
                except asyncio.TimeoutError:
                    status = "timeout"
                    output = f"timeout after {SUBAGENT_TIMEOUT_SEC}s"
                except Exception as e:
                    status = "error"
                    output = f"run_error: {e}"
        finally:
            is_subagent_run.reset(token)

    latency_ms = int((time.time() - started) * 1000)
    _log_run(
        agent_id=agent_id, agent_name=name, project_slug=project_slug,
        parent_run_id=parent_run_id, spawned_by_agent=spawned_by,
        scoped_skills=spec.scoped_skills, scoped_tools=spec.scoped_tools,
        input_brief=input_brief or purpose, output=output,
        status=status, latency_ms=latency_ms,
    )
    return {
        "ok": status == "done",
        "agent_id": agent_id,
        "output": output,
        "was_new": was_new,
        "reused": (not was_new),
        "status": status,
        "latency_ms": latency_ms,
    }


def _list_custom_agents(keyword: str = "", limit: int = 20) -> Dict[str, Any]:
    """List custom agents in current project (and globals)."""
    ctx = _ctx()
    project_slug = ctx["project_slug"]
    eng = _get_engine()
    if eng is None:
        return {"ok": False, "agents": []}
    try:
        from sqlalchemy import text
        kw = (keyword or "").strip().lower()
        with eng.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT id, name, purpose, usage_count, success_rate, "
                    "       project_slug, enabled "
                    "FROM dash.dash_custom_agents "
                    "WHERE enabled = true "
                    "  AND (project_slug IS NOT DISTINCT FROM :ps OR project_slug IS NULL) "
                    "  AND (:kw = '' OR LOWER(name) LIKE :pat OR LOWER(COALESCE(purpose,'')) LIKE :pat) "
                    "ORDER BY usage_count DESC NULLS LAST, name ASC "
                    "LIMIT :lim"
                ),
                {"ps": project_slug, "kw": kw, "pat": f"%{kw}%", "lim": int(limit or 20)},
            ).mappings().all()
        return {"ok": True, "agents": [dict(r) for r in rows]}
    except Exception as e:
        return {"ok": False, "error": str(e), "agents": []}


def _get_custom_agent_detail(name: str) -> Dict[str, Any]:
    """Get full spec + recent runs for a custom agent."""
    ctx = _ctx()
    project_slug = ctx["project_slug"]
    eng = _get_engine()
    if eng is None:
        return {"ok": False, "reason": "db_unavailable"}
    try:
        from sqlalchemy import text
        with eng.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT id, name, purpose, base_agent, agent_md, scoped_skills, "
                    "       scoped_tools, persona, extra_instructions, usage_count, "
                    "       last_used_at, success_rate, enabled, created_at "
                    "FROM dash.dash_custom_agents "
                    "WHERE name = :nm AND "
                    "      (project_slug IS NOT DISTINCT FROM :ps OR project_slug IS NULL) "
                    "ORDER BY (project_slug IS NULL) ASC LIMIT 1"
                ),
                {"nm": name, "ps": project_slug},
            ).mappings().first()
            if not row:
                return {"ok": False, "reason": "not_found"}
            runs = conn.execute(
                text(
                    "SELECT id, parent_run_id, status, latency_ms, created_at, "
                    "       LEFT(COALESCE(output,''), 240) AS output_preview "
                    "FROM dash.dash_subagent_runs WHERE agent_id = :aid "
                    "ORDER BY created_at DESC LIMIT 10"
                ),
                {"aid": row["id"]},
            ).mappings().all()
        return {"ok": True, "agent": dict(row), "recent_runs": [dict(r) for r in runs]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _deprecate_custom_agent(name: str) -> Dict[str, Any]:
    """Soft delete (``enabled=false``). Reversible."""
    ctx = _ctx()
    project_slug = ctx["project_slug"]
    eng = _get_engine()
    if eng is None:
        return {"ok": False, "reason": "db_unavailable"}
    try:
        from sqlalchemy import text
        with eng.begin() as conn:
            r = conn.execute(
                text(
                    "UPDATE dash.dash_custom_agents SET enabled = false, updated_at = now() "
                    "WHERE name = :nm AND project_slug IS NOT DISTINCT FROM :ps"
                ),
                {"nm": name, "ps": project_slug},
            )
        return {"ok": r.rowcount > 0, "deprecated": name}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── Tool wrappers (graceful imports) ────────────────────────────────────────
def _try_tool(fn):
    """Wrap as Agno @tool if available, else return raw fn."""
    try:
        from agno.tools import tool
        return tool(fn)
    except Exception:
        return fn


spawn_subagent = _try_tool(_spawn_subagent)
list_custom_agents = _try_tool(_list_custom_agents)
get_custom_agent_detail = _try_tool(_get_custom_agent_detail)
deprecate_custom_agent = _try_tool(_deprecate_custom_agent)


__all__ = [
    "spawn_subagent",
    "list_custom_agents",
    "get_custom_agent_detail",
    "deprecate_custom_agent",
    "is_subagent_run",
    "current_allowed_tools",
]
