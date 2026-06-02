"""Dash-OS Phase 2A — Reporter file download + list endpoints."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/reporter", tags=["reporter"])


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


def _can_access(file_row, user) -> bool:
    if not user:
        return False
    if user.get("is_super_admin"):
        return True
    if file_row.get("user_id") == user.get("id"):
        return True
    # project-level access check is best-effort; defer to project share table elsewhere
    return False


@router.get("/files/{file_id}")
def download_file(file_id: str, user=Depends(_get_user)):
    eng = _get_engine()
    if eng is None:
        raise HTTPException(503, "db unavailable")
    from sqlalchemy import text
    with eng.connect() as conn:
        row = conn.execute(
            text(
                "SELECT id, project_slug, user_id, file_type, filename, storage_path, expires_at "
                "FROM dash.dash_generated_files WHERE id = :id"
            ),
            {"id": file_id},
        ).mappings().first()
    if not row:
        raise HTTPException(404, "file_not_found")
    if not _can_access(dict(row), user):
        raise HTTPException(403, "forbidden")
    path = Path(row["storage_path"])
    if not path.exists():
        raise HTTPException(410, "file_evicted")
    return FileResponse(str(path), filename=row["filename"], media_type="application/octet-stream")


@router.get("/files")
def list_files(
    project_slug: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=200),
    user=Depends(_get_user),
):
    eng = _get_engine()
    if eng is None:
        return {"ok": False, "error": "db unavailable", "files": []}
    from sqlalchemy import text
    with eng.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT id, project_slug, file_type, filename, size_bytes, created_at, expires_at
                FROM dash.dash_generated_files
                WHERE (:ps IS NULL OR project_slug = :ps)
                  AND (:uid IS NULL OR user_id = :uid OR :is_admin)
                  AND (expires_at IS NULL OR expires_at > now())
                ORDER BY created_at DESC
                LIMIT :lim
                """
            ),
            {
                "ps": project_slug,
                "uid": user.get("id") if user else None,
                "is_admin": bool(user and user.get("is_super_admin")),
                "lim": limit,
            },
        ).mappings().all()
    return {"ok": True, "files": [dict(r) for r in rows]}


@router.delete("/files/{file_id}")
def soft_delete(file_id: str, user=Depends(_get_user)):
    eng = _get_engine()
    if eng is None:
        raise HTTPException(503, "db unavailable")
    from sqlalchemy import text
    with eng.begin() as conn:
        row = conn.execute(
            text("SELECT user_id FROM dash.dash_generated_files WHERE id=:id"),
            {"id": file_id},
        ).first()
        if not row:
            raise HTTPException(404, "file_not_found")
        if not user or (user.get("id") != row[0] and not user.get("is_super_admin")):
            raise HTTPException(403, "forbidden")
        conn.execute(
            text("UPDATE dash.dash_generated_files SET expires_at = now() WHERE id=:id"),
            {"id": file_id},
        )
    return {"ok": True, "deleted": file_id}
