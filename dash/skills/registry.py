"""Skill registry + loader.

Public API:
- list_skills(project_slug, agent_name, category) -> [skill_meta]
- find_skills_for(question, project_slug, agent_name, top_k=3) -> [skill]
- load_skill(skill_id) -> {instructions, tools, name}
- register_skill(meta) -> id
- @skill decorator for in-code skills (auto-register on import)

Behind EXPERIMENTAL_AGI=1: load_skill tool surfaces full skill body.
Otherwise: returns only metadata stub (skill enumeration still works).
"""
from __future__ import annotations

import hashlib
import json as _json
import logging
import os
import secrets
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


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
            current_project_slug, current_user_id, current_run_id, current_agent_name,
        )
        return {
            "project_slug": current_project_slug.get(),
            "user_id": current_user_id.get(),
            "run_id": current_run_id.get(),
            "agent_name": current_agent_name.get(),
        }
    except Exception:
        return {"project_slug": None, "user_id": None, "run_id": None, "agent_name": None}


# ── In-process registry (decorator-based) ───────────────────────────────
_in_proc_skills: Dict[str, Dict[str, Any]] = {}


def skill(
    name: str,
    category: str = "meta",
    trigger_keywords: Optional[List[str]] = None,
    description: str = "",
):
    """Decorator: register a Python-defined skill at import time."""
    def _decorator(fn_or_text):
        if callable(fn_or_text):
            instructions = fn_or_text.__doc__ or ""
            tools = [{"name": fn_or_text.__name__, "fn_module": fn_or_text.__module__,
                      "fn_name": fn_or_text.__name__}]
        else:
            instructions = str(fn_or_text)
            tools = []
        slug = "skl_" + hashlib.sha256(name.encode()).hexdigest()[:8]
        _in_proc_skills[slug] = {
            "id": slug, "name": name, "category": category,
            "trigger_keywords": trigger_keywords or [],
            "instructions": instructions, "tools": tools,
            "is_builtin": True, "enabled": True,
        }
        return fn_or_text
    return _decorator


def register_skill(meta: Dict[str, Any]) -> str:
    """Persist a skill to DB. Returns id."""
    eng = _get_engine()
    sid = meta.get("id") or ("skl_" + secrets.token_hex(4))
    if eng is None:
        _in_proc_skills[sid] = {**meta, "id": sid}
        return sid
    try:
        from sqlalchemy import text
        with eng.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO dash.dash_skills
                      (id, project_slug, name, category, description, trigger_keywords,
                       instructions, tools, is_builtin, runtime_role)
                    VALUES (:id, :ps, :nm, :cat, :ds, CAST(:tk AS jsonb),
                            :inst, CAST(:tools AS jsonb), :ib, :rr)
                    ON CONFLICT (id) DO UPDATE
                      SET description = EXCLUDED.description,
                          trigger_keywords = EXCLUDED.trigger_keywords,
                          instructions = EXCLUDED.instructions,
                          tools = EXCLUDED.tools,
                          runtime_role = EXCLUDED.runtime_role,
                          updated_at = now()
                    """
                ),
                {
                    "id": sid, "ps": meta.get("project_slug"),
                    "nm": meta["name"], "cat": meta.get("category", "meta"),
                    "ds": meta.get("description"),
                    "tk": _json.dumps(meta.get("trigger_keywords") or []),
                    "inst": meta["instructions"],
                    "tools": _json.dumps(meta.get("tools") or []),
                    "ib": bool(meta.get("is_builtin", False)),
                    "rr": meta.get("runtime_role", "agent_hint"),
                },
            )
    except Exception as e:
        logger.warning("register_skill DB failed: %s", e)
    # Issue #12: drop _skill_prefix cache for this skill so DeepDashAgent etc.
    # pick up the new instructions on the next stage call (not after 5min TTL).
    try:
        from dash.dashboards.agent import invalidate_skill_cache as _inv
        _inv(sid)
    except ImportError:
        pass
    except Exception as _e:
        logger.debug("invalidate_skill_cache(%s) skipped: %s", sid, _e)
    return sid


def list_skills(
    project_slug: Optional[str] = None,
    agent_name: Optional[str] = None,
    category: Optional[str] = None,
    enabled_only: bool = True,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    eng = _get_engine()
    if eng is None:
        # in-proc fallback
        out = [
            v for v in _in_proc_skills.values()
            if (not category or v.get("category") == category)
            and (not enabled_only or v.get("enabled", True))
        ]
        return out
    try:
        from sqlalchemy import text
        with eng.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT s.id, s.name, s.category, s.description, s.trigger_keywords,
                           s.is_builtin, s.enabled, s.project_slug, s.created_at,
                           s.updated_at,
                           (SELECT COUNT(*) FROM dash.dash_skill_invocations i
                              WHERE i.skill_id = s.id AND i.created_at > now() - INTERVAL '30 days'
                           ) AS invocations_30d
                    FROM dash.dash_skills s
                    WHERE (CAST(:ps AS TEXT) IS NULL OR s.project_slug = :ps OR s.project_slug IS NULL)
                      AND (CAST(:cat AS TEXT) IS NULL OR s.category = :cat)
                      AND (:eo = false OR s.enabled = true)
                    ORDER BY s.is_builtin DESC, s.name
                    """
                ),
                {"ps": project_slug, "cat": category, "eo": enabled_only},
            ).mappings().all()
        out = [dict(r) for r in rows]
        if agent_name:
            # filter by bindings (allow if no binding = open to all)
            with eng.connect() as conn:
                bind_rows = conn.execute(
                    text(
                        "SELECT skill_id FROM dash.dash_skill_bindings "
                        "WHERE enabled=true AND agent_name IN (:an, '*')"
                    ),
                    {"an": agent_name},
                ).all()
            bound = {r[0] for r in bind_rows}
            if bound:
                out = [s for s in out if s["id"] in bound]
    except Exception as e:
        logger.warning("list_skills failed: %s", e)
    return out


def find_skills_for(
    question: str, project_slug: Optional[str] = None,
    agent_name: Optional[str] = None, top_k: int = 3,
) -> List[Dict[str, Any]]:
    """Score skills by trigger_keywords overlap with question.

    Scoring:
    - 3 points: full multi-word phrase appears as substring
    - 1 point per word from a multi-word keyword that appears in question
    - 1 point for single-word keyword matched as whole word
    """
    import re as _re
    q_lower = question.lower()
    q_words = set(_re.findall(r"[a-z0-9_]+", q_lower))
    skills = list_skills(project_slug=project_slug, agent_name=agent_name)
    scored = []
    for s in skills:
        kws = s.get("trigger_keywords") or []
        if isinstance(kws, str):
            try:
                kws = _json.loads(kws)
            except Exception:
                kws = []
        score = 0
        for kw in kws:
            kw_lower = str(kw).lower().strip()
            if not kw_lower:
                continue
            if kw_lower in q_lower:
                score += 3
                continue
            kw_words = set(_re.findall(r"[a-z0-9_]+", kw_lower))
            if not kw_words:
                continue
            overlap = len(kw_words & q_words)
            if overlap and overlap >= min(2, len(kw_words)):
                score += overlap
        if score > 0:
            scored.append((score, s))
    scored.sort(key=lambda x: -x[0])
    return [s for _, s in scored[:top_k]]


def load_skill(
    skill_id: str,
    agent_name: Optional[str] = None,
    audit: bool = True,
) -> Dict[str, Any]:
    """Fetch full skill body for injection into agent context.

    Issue #13: `audit=True` (default, backward-compat) writes a row to
    `dash_skill_invocations` but is sampled at 10% to avoid 5 audit writes
    per dashboard build (one per pipeline skill). Pipeline callers
    (`_skill_prefix` in dash/dashboards/agent.py) pass `audit=False` to skip
    audit entirely — stage-level pipeline telemetry already lives in
    `dash_training_runs`. Set `audit=True` and rely on the 10% sampling for
    callers that want a representative trace without flooding the table.
    """
    started = time.time()
    eng = _get_engine()
    skill = None
    if eng is not None:
        try:
            from sqlalchemy import text
            with eng.connect() as conn:
                row = conn.execute(
                    text("SELECT * FROM dash.dash_skills WHERE id=:id AND enabled=true"),
                    {"id": skill_id},
                ).mappings().first()
            skill = dict(row) if row else None
        except Exception as e:
            logger.warning("load_skill failed: %s", e)
    if skill is None:
        skill = _in_proc_skills.get(skill_id)
    if not skill:
        return {"ok": False, "error": "skill_not_found"}

    # Audit (Issue #13: sample at 10% to avoid 5 audit writes per dashboard
    # build; pipeline callers pass audit=False to opt out entirely).
    ctx = _ctx()
    instructions = skill.get("instructions") or ""
    if audit and eng is not None:
        import random as _rand
        if _rand.random() < 0.1:
            try:
                from sqlalchemy import text
                with eng.begin() as conn:
                    conn.execute(
                        text(
                            """
                            INSERT INTO dash.dash_skill_invocations
                              (skill_id, agent_name, project_slug, user_id, run_id,
                               loaded_chars, latency_ms)
                            VALUES (:sid, :an, :ps, :uid, :rid, :lc, :lat)
                            """
                        ),
                        {
                            "sid": skill_id, "an": agent_name or ctx["agent_name"],
                            "ps": ctx["project_slug"], "uid": ctx["user_id"],
                            "rid": ctx["run_id"], "lc": len(instructions),
                            "lat": int((time.time() - started) * 1000),
                        },
                    )
            except Exception:
                pass

    # When flag off: return metadata only, no full body injection
    if not _enabled():
        return {
            "ok": True, "stub": True, "id": skill_id,
            "name": skill["name"], "category": skill.get("category"),
            "loaded_chars": 0,
        }

    return {
        "ok": True, "id": skill_id, "name": skill["name"],
        "category": skill.get("category"), "instructions": instructions,
        "tools": skill.get("tools") or [], "loaded_chars": len(instructions),
    }


# ── Agno @tool wrappers for agent use ───────────────────────────────────
def _try_tool(fn):
    try:
        from agno.tools import tool
        return tool(fn)
    except Exception:
        return fn


@_try_tool
def discover_skills(question: str = "", top_k: int = 3) -> Dict[str, Any]:
    """Return top-N skills relevant to the user's question."""
    ctx = _ctx()
    skills = find_skills_for(
        question, project_slug=ctx["project_slug"],
        agent_name=ctx["agent_name"], top_k=top_k,
    )
    return {
        "ok": True,
        "skills": [
            {"id": s["id"], "name": s["name"], "category": s.get("category"),
             "description": s.get("description")}
            for s in skills
        ],
    }


@_try_tool
def load_skill_tool(skill_id: str) -> Dict[str, Any]:
    """Lazy-load full skill instructions + tools for current agent turn."""
    ctx = _ctx()
    return load_skill(skill_id, agent_name=ctx["agent_name"])
