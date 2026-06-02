"""Dash-OS Phase 10 — Skill draft approval queue API.

Endpoints:
  GET    /api/skill-drafts?status=pending&limit=50
  GET    /api/skill-drafts/{id}
  POST   /api/skill-drafts/{id}/approve
  POST   /api/skill-drafts/{id}/reject     body: {reason}
  POST   /api/skill-drafts/{id}/re-verify

Backed by `dash.dash_skill_drafts` (migration 052). Drafter/verifier modules
in `dash.skills.drafter` / `dash.skills.verifier` may not exist yet at
runtime — all calls are fail-soft.
"""
from __future__ import annotations

import json as _json
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/skill-drafts", tags=["skill-drafts"])


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


class RejectIn(BaseModel):
    reason: Optional[str] = None


def _row_to_dict(row) -> Dict[str, Any]:
    d = dict(row)
    # JSONB cols already decoded by psycopg; pass through. Strings stay strings.
    return d


@router.get("")
def list_drafts(
    status: Optional[str] = Query(None),
    project_slug: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    user=Depends(_get_user),
):
    eng = _get_engine()
    if eng is None:
        return {"drafts": []}
    from sqlalchemy import text
    sql = (
        "SELECT id, project_slug, source_run_id, drafted_by_agent, trigger_phrase, "
        "iteration, proposed_name, proposed_description, frontmatter, verifier_results, "
        "status, rejection_reason, promoted_skill_id, approved_by, approved_at, created_at "
        "FROM dash.dash_skill_drafts "
        "WHERE (CAST(:status AS TEXT) IS NULL OR status = :status) "
        "AND (CAST(:slug AS TEXT) IS NULL OR project_slug = :slug) "
        "ORDER BY created_at DESC LIMIT :lim"
    )
    with eng.connect() as conn:
        rows = conn.execute(
            text(sql),
            {"status": status, "slug": project_slug, "lim": limit},
        ).mappings().all()
    return {"drafts": [_row_to_dict(r) for r in rows]}


@router.get("/{draft_id}")
def get_draft(draft_id: str, user=Depends(_get_user)):
    eng = _get_engine()
    if eng is None:
        raise HTTPException(503, "db unavailable")
    from sqlalchemy import text
    with eng.connect() as conn:
        row = conn.execute(
            text("SELECT * FROM dash.dash_skill_drafts WHERE id=:id"),
            {"id": draft_id},
        ).mappings().first()
    if not row:
        raise HTTPException(404, "draft_not_found")
    return _row_to_dict(row)


@router.post("/{draft_id}/approve")
def approve_draft(draft_id: str, body: Optional[Dict[str, Any]] = None, user=Depends(_get_user)):
    uid = (user or {}).get("id", 0)
    try:
        from dash.skills.drafter import approve_draft as _approve
    except Exception as e:
        logger.info("drafter unavailable on approve: %s", e)
        return {"ok": False, "error": "drafter_unavailable"}
    try:
        result = _approve(draft_id, uid)
        if isinstance(result, dict):
            return {"ok": True, **result}
        return {"ok": True, "result": result}
    except Exception as e:
        logger.warning("approve_draft failed: %s", e)
        return {"ok": False, "error": str(e)}


@router.post("/{draft_id}/reject")
def reject_draft(draft_id: str, body: RejectIn, user=Depends(_get_user)):
    uid = (user or {}).get("id", 0)
    reason = (body.reason or "").strip() if body else ""
    try:
        from dash.skills.drafter import reject_draft as _reject
    except Exception as e:
        logger.info("drafter unavailable on reject: %s", e)
        return {"ok": False, "error": "drafter_unavailable"}
    try:
        result = _reject(draft_id, reason, uid)
        if isinstance(result, dict):
            return {"ok": True, **result}
        return {"ok": True, "result": result}
    except Exception as e:
        logger.warning("reject_draft failed: %s", e)
        return {"ok": False, "error": str(e)}


@router.post("/{draft_id}/re-verify")
def reverify_draft(draft_id: str, user=Depends(_get_user)):
    """Re-run verifier on the draft and persist results."""
    eng = _get_engine()
    if eng is None:
        raise HTTPException(503, "db unavailable")
    from sqlalchemy import text
    with eng.connect() as conn:
        row = conn.execute(
            text("SELECT * FROM dash.dash_skill_drafts WHERE id=:id"),
            {"id": draft_id},
        ).mappings().first()
    if not row:
        raise HTTPException(404, "draft_not_found")
    try:
        from dash.skills.verifier import verify_draft as _verify
    except Exception as e:
        logger.info("verifier unavailable: %s", e)
        return {"ok": False, "error": "verifier_unavailable"}
    try:
        verifier_results = _verify(dict(row))
        # Persist + flip status if currently pending
        with eng.begin() as conn:
            conn.execute(
                text(
                    "UPDATE dash.dash_skill_drafts "
                    "SET verifier_results = CAST(:r AS jsonb), "
                    "    status = CASE WHEN status IN ('pending','verifying') "
                    "                  THEN 'verified' ELSE status END "
                    "WHERE id = :id"
                ),
                {"r": _json.dumps(verifier_results), "id": draft_id},
            )
        return {"ok": True, "verifier_results": verifier_results}
    except Exception as e:
        logger.warning("re-verify failed: %s", e)
        return {"ok": False, "error": str(e)}
