"""Skill resolver router — LLM intent-classification skill picker.

Endpoints:
  POST /api/resolver/resolve     — {project, query, top_k?} → {chosen, candidates, reason}
  GET  /api/resolver/candidates  — ?project=  → [{name, description, tags}]

Registered in app/main.py with try/except guard.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/resolver", tags=["resolver"])


def _user_dep():
    try:
        from app.auth import _get_user
        return _get_user
    except Exception:
        def _noop():
            return {"id": 0, "username": "anonymous", "is_super_admin": False}
        return _noop


_get_user = _user_dep()


class ResolveIn(BaseModel):
    project: Optional[str] = None
    query: str
    top_k: Optional[int] = 3


@router.post("/resolve")
def resolve_skill(body: ResolveIn, user=Depends(_get_user)) -> Dict[str, Any]:
    if not body.query or not body.query.strip():
        raise HTTPException(400, "query is required")
    try:
        from dash.skills.resolver import resolve as _resolve
    except Exception as e:
        logger.warning("resolver import failed: %s", e)
        raise HTTPException(503, "resolver unavailable")
    top_k = body.top_k if body.top_k and body.top_k > 0 else 3
    try:
        result = _resolve(body.query, project=body.project, top_k=top_k)
    except Exception as e:
        logger.exception("resolver.resolve crashed")
        raise HTTPException(500, f"resolver failed: {e}")
    return result


@router.get("/candidates")
def candidates(project: Optional[str] = Query(None), user=Depends(_get_user)) -> Dict[str, List[Dict[str, Any]]]:
    try:
        from dash.skills.resolver import list_candidate_skills
    except Exception as e:
        logger.warning("resolver import failed: %s", e)
        raise HTTPException(503, "resolver unavailable")
    try:
        skills = list_candidate_skills(project)
    except Exception as e:
        logger.exception("list_candidate_skills crashed")
        raise HTTPException(500, f"candidates failed: {e}")
    return {"candidates": skills}
