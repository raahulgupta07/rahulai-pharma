"""
Suggested Rules API
===================

List, approve, and reject AI-suggested business rules.
"""

import json
import time

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import create_engine as _sa_create_engine, text
from sqlalchemy.pool import NullPool

from dash.paths import KNOWLEDGE_DIR
from db import db_url

router = APIRouter(prefix="/api/projects", tags=["Suggested Rules"])
_engine = _sa_create_engine(db_url, poolclass=NullPool)


def _get_user(request: Request) -> dict:
    user = getattr(getattr(request, 'state', None), 'user', None)
    if not user:
        raise HTTPException(401, "Not authenticated")
    return user


def _check_access(user: dict, slug: str):
    from app.auth import check_project_permission
    if not check_project_permission(user, slug):
        raise HTTPException(403, "Access denied")


@router.get("/{slug}/suggested-rules")
def list_suggested_rules(slug: str, request: Request):
    """List pending suggested rules for a project."""
    _get_user(request)
    with _engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, name, type, definition, source_session_id, status, created_at "
            "FROM public.dash_suggested_rules WHERE project_slug = :slug AND status = 'pending' "
            "ORDER BY created_at DESC LIMIT 50"
        ), {"slug": slug}).fetchall()

    suggestions = [
        {"id": r[0], "name": r[1], "type": r[2], "definition": r[3],
         "session_id": r[4], "status": r[5], "created_at": str(r[6]) if r[6] else None}
        for r in rows
    ]
    return {"suggestions": suggestions}


@router.post("/{slug}/suggested-rules/{rule_id}/approve")
def approve_suggested_rule(slug: str, rule_id: int, request: Request):
    """Approve a suggested rule — creates it as a real rule file."""
    user = _get_user(request)
    _check_access(user, slug)
    with _engine.connect() as conn:
        row = conn.execute(text(
            "SELECT name, type, definition FROM public.dash_suggested_rules WHERE id = :id AND project_slug = :slug"
        ), {"id": rule_id, "slug": slug}).fetchone()

        if not row:
            raise HTTPException(404, "Suggestion not found")

        # Create rule file
        rules_dir = KNOWLEDGE_DIR / slug / "rules"
        rules_dir.mkdir(parents=True, exist_ok=True)
        file_id = f"rule_{int(time.time() * 1000)}"
        rule = {
            "id": file_id,
            "name": row[0],
            "type": row[1],
            "definition": row[2],
            "source": "ai_suggested",
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        with open(rules_dir / f"{file_id}.json", "w") as f:
            json.dump(rule, f, indent=2)

        # Mark as approved
        conn.execute(text(
            "UPDATE public.dash_suggested_rules SET status = 'approved' WHERE id = :id"
        ), {"id": rule_id})
        conn.commit()

    # Re-index
    try:
        from app.rules import _reindex_rules
        _reindex_rules(slug)
    except Exception:
        pass

    return {"status": "ok", "rule": rule}


@router.post("/{slug}/suggested-rules/{rule_id}/reject")
def reject_suggested_rule(slug: str, rule_id: int, request: Request):
    """Reject a suggested rule."""
    user = _get_user(request)
    _check_access(user, slug)
    with _engine.connect() as conn:
        conn.execute(text(
            "UPDATE public.dash_suggested_rules SET status = 'rejected' WHERE id = :id AND project_slug = :slug"
        ), {"id": rule_id, "slug": slug})
        conn.commit()
    return {"status": "ok"}
