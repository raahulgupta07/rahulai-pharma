"""Dash-OS Phase 4 — Skills CRUD + invocation stats."""
from __future__ import annotations

import json as _json
import logging
import secrets
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/skills", tags=["skills"])


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


def _user_dep():
    try:
        from app.auth import _get_user
        return _get_user
    except Exception:
        def _noop():
            return {"id": 0, "username": "anonymous", "is_super_admin": False}
        return _noop


_get_user = _user_dep()


class SkillIn(BaseModel):
    project_slug: Optional[str] = None
    name: str
    category: Optional[str] = "meta"
    description: Optional[str] = None
    trigger_keywords: List[str] = []
    instructions: str
    tools: List[Dict[str, Any]] = []


class BindingIn(BaseModel):
    bindings: List[Dict[str, Any]]   # [{agent_name, enabled}]


@router.get("")
def list_skills(
    project_slug: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    agent_name: Optional[str] = Query(None),
    user=Depends(_get_user),
):
    from dash.skills.registry import list_skills as _list
    return {"skills": _list(project_slug=project_slug, agent_name=agent_name, category=category)}


@router.post("")
def create_skill(body: SkillIn, user=Depends(_get_user)):
    from dash.skills.registry import register_skill
    sid = register_skill({
        **body.dict(), "is_builtin": False,
    })
    return {"ok": True, "id": sid}


@router.get("/{sid}")
def get_skill(sid: str, user=Depends(_get_user)):
    eng = _get_engine()
    if eng is None:
        raise HTTPException(503, "db unavailable")
    from sqlalchemy import text
    with eng.connect() as conn:
        row = conn.execute(
            text("SELECT * FROM dash.dash_skills WHERE id=:id"),
            {"id": sid},
        ).mappings().first()
    if not row:
        raise HTTPException(404, "skill_not_found")
    return dict(row)


@router.patch("/{sid}")
def patch_skill(sid: str, body: Dict[str, Any], user=Depends(_get_user)):
    eng = _get_engine()
    if eng is None:
        raise HTTPException(503, "db unavailable")
    allowed = {"name", "category", "description", "trigger_keywords", "instructions",
               "tools", "enabled"}
    fields = {k: v for k, v in body.items() if k in allowed and v is not None}
    if not fields:
        return {"ok": True, "patched": 0}
    from sqlalchemy import text
    parts = []
    params = {"id": sid}
    for k, v in fields.items():
        if k in ("trigger_keywords", "tools"):
            parts.append(f"{k}=CAST(:{k} AS jsonb)")
            params[k] = _json.dumps(v)
        else:
            parts.append(f"{k}=:{k}")
            params[k] = v
    sets = ", ".join(parts)
    with eng.begin() as conn:
        r = conn.execute(
            text(f"UPDATE dash.dash_skills SET {sets}, updated_at=now() WHERE id=:id"),
            params,
        )
    return {"ok": r.rowcount > 0, "patched": r.rowcount}


@router.delete("/{sid}")
def delete_skill(sid: str, user=Depends(_get_user)):
    eng = _get_engine()
    if eng is None:
        raise HTTPException(503, "db unavailable")
    from sqlalchemy import text
    if user and not user.get("is_super_admin"):
        with eng.connect() as conn:
            row = conn.execute(
                text("SELECT is_builtin FROM dash.dash_skills WHERE id=:id"),
                {"id": sid},
            ).first()
            if row and row[0]:
                raise HTTPException(403, "cannot_delete_builtin")
    with eng.begin() as conn:
        r = conn.execute(text("DELETE FROM dash.dash_skills WHERE id=:id"), {"id": sid})
    return {"ok": r.rowcount > 0}


@router.get("/{sid}/bindings")
def list_bindings(sid: str, user=Depends(_get_user)):
    eng = _get_engine()
    if eng is None:
        return {"bindings": []}
    from sqlalchemy import text
    with eng.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT agent_name, enabled FROM dash.dash_skill_bindings "
                "WHERE skill_id=:sid ORDER BY agent_name"
            ),
            {"sid": sid},
        ).mappings().all()
    return {"bindings": [dict(r) for r in rows]}


@router.patch("/{sid}/bindings")
def patch_bindings(sid: str, body: BindingIn, user=Depends(_get_user)):
    eng = _get_engine()
    if eng is None:
        raise HTTPException(503, "db unavailable")
    from sqlalchemy import text
    with eng.begin() as conn:
        for b in body.bindings:
            conn.execute(
                text(
                    """
                    INSERT INTO dash.dash_skill_bindings (skill_id, agent_name, enabled)
                    VALUES (:sid, :an, :en)
                    ON CONFLICT (skill_id, agent_name) DO UPDATE SET enabled = EXCLUDED.enabled
                    """
                ),
                {"sid": sid, "an": b["agent_name"], "en": bool(b.get("enabled", True))},
            )
    return {"ok": True, "patched": len(body.bindings)}


@router.get("/discover/for")
def discover_for(
    question: str = Query(...),
    project_slug: Optional[str] = Query(None),
    agent_name: Optional[str] = Query(None),
    top_k: int = Query(3, ge=1, le=10),
    user=Depends(_get_user),
):
    from dash.skills.registry import find_skills_for
    return {"skills": find_skills_for(question, project_slug, agent_name, top_k)}


@router.get("/invocations")
def list_invocations(
    skill_id: Optional[str] = Query(None),
    days: int = Query(14, ge=1, le=90),
    limit: int = Query(200, ge=1, le=1000),
    user=Depends(_get_user),
):
    eng = _get_engine()
    if eng is None:
        return {"invocations": []}
    from sqlalchemy import text
    with eng.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT skill_id, agent_name, project_slug, user_id, loaded_chars,
                       latency_ms, created_at
                FROM dash.dash_skill_invocations
                WHERE created_at > now() - (:d || ' days')::interval
                  AND (:sid IS NULL OR skill_id = :sid)
                ORDER BY created_at DESC LIMIT :lim
                """
            ),
            {"d": days, "sid": skill_id, "lim": limit},
        ).mappings().all()
    return {"invocations": [dict(r) for r in rows]}


@router.on_event("startup")
def _register_builtins_on_startup():
    try:
        from dash.skills.builtin import register_builtins
        n = register_builtins()
        logger.info("skills builtins registered: %d", n)
    except Exception as e:
        logger.warning("skills builtin registration failed: %s", e)
