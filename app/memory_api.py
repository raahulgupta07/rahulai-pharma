"""Dash-OS Phase 7 — Entity memory + agentic state CRUD + audit endpoints."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/memory", tags=["memory"])


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


# ── Entity memory ───────────────────────────────────────────────────────
class EntityFactIn(BaseModel):
    project_slug: Optional[str] = None
    entity_type: str
    entity_id: str
    fact: str
    fact_kind: str = "observation"
    confidence: float = 0.7
    metadata: Optional[Dict[str, Any]] = None


@router.post("/entity")
def add_entity_fact(body: EntityFactIn, user=Depends(_get_user)):
    from dash.memory.entity import remember
    return remember(
        entity_type=body.entity_type, entity_id=body.entity_id, fact=body.fact,
        project_slug=body.project_slug, fact_kind=body.fact_kind,
        confidence=body.confidence, source="user",
        metadata=body.metadata, user_id=user.get("id") if user else None,
    )


@router.get("/entity/{entity_type}/{entity_id}")
def get_entity_facts(
    entity_type: str, entity_id: str,
    project_slug: Optional[str] = Query(None),
    fact_kind: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    user=Depends(_get_user),
):
    from dash.memory.entity import recall
    facts = recall(entity_type, entity_id, project_slug, fact_kind, limit)
    return {"facts": facts, "count": len(facts)}


@router.get("/entity/{entity_type}/search")
def semantic_search_entity(
    entity_type: str,
    query: str = Query(...),
    project_slug: Optional[str] = Query(None),
    top_k: int = Query(10, ge=1, le=50),
    user=Depends(_get_user),
):
    from dash.memory.entity import semantic_recall
    return {"results": semantic_recall(entity_type, query, project_slug, top_k)}


@router.delete("/entity/{memory_id}")
def delete_entity_fact(memory_id: int, user=Depends(_get_user)):
    from dash.memory.entity import forget
    ok = forget(memory_id)
    if not ok:
        raise HTTPException(404, "not_found")
    return {"ok": True, "archived": memory_id}


@router.post("/entity/{memory_id}/promote")
def promote_entity_fact(memory_id: int, user=Depends(_get_user)):
    from dash.memory.entity import promote_to_project
    return promote_to_project(memory_id)


# ── Agentic state ───────────────────────────────────────────────────────
class StateIn(BaseModel):
    session_id: str
    agent_name: str
    key: str
    value: Any


@router.post("/state")
def set_state_endpoint(body: StateIn, user=Depends(_get_user)):
    from dash.memory.agentic_state import set_state
    ok = set_state(body.session_id, body.agent_name, body.key, body.value)
    return {"ok": ok}


@router.get("/state/{session_id}")
def list_state_endpoint(
    session_id: str,
    agent_name: Optional[str] = Query(None),
    user=Depends(_get_user),
):
    from dash.memory.agentic_state import list_state
    return {"state": list_state(session_id, agent_name)}


@router.delete("/state/{session_id}")
def clear_state_endpoint(session_id: str, user=Depends(_get_user)):
    from dash.memory.agentic_state import clear_session
    n = clear_session(session_id)
    return {"ok": True, "deleted": n}


# ── Run context audit ────────────────────────────────────────────────────
@router.get("/run-context/audit")
def list_run_context_audit(
    days: int = Query(7, ge=1, le=90),
    project_slug: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=1000),
    user=Depends(_get_user),
):
    eng = _get_engine()
    if eng is None:
        return {"audit": []}
    from sqlalchemy import text
    with eng.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT run_id, project_slug, user_id, agent_name, scope_id,
                       query_intent, trigger_kind, created_at
                FROM dash.dash_run_context_audit
                WHERE created_at > now() - (:d || ' days')::interval
                  AND (:ps IS NULL OR project_slug = :ps)
                ORDER BY created_at DESC LIMIT :lim
                """
            ),
            {"d": days, "ps": project_slug, "lim": limit},
        ).mappings().all()
    return {"audit": [dict(r) for r in rows]}
