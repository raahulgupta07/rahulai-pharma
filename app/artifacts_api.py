"""Artifact Gallery API — list, download, thumbnail, delete.

All endpoints under ``/api/artifacts``. Reuses ``dash.artifacts.registry``
which extends ``dash_generated_files`` (042 + 054).
"""
from __future__ import annotations

import io
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Query
from fastapi.responses import Response, StreamingResponse

from dash.artifacts import registry as art

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/artifacts", tags=["artifacts"])


def _get_user(request: Request) -> Optional[dict]:
    user = getattr(getattr(request, "state", None), "user", None)
    if user:
        return user
    try:
        from app.auth import get_current_user  # type: ignore
        return get_current_user(request)
    except Exception:
        return None


def _require_user(request: Request) -> dict:
    u = _get_user(request)
    if not u:
        raise HTTPException(401, "Not authenticated")
    return u


def _can_access(meta: dict, user: Optional[dict]) -> bool:
    """Project-scoped access. Super admins always allowed."""
    if not user:
        return False
    if user.get("is_super") or user.get("is_super_admin"):
        return True
    # Owner direct access
    uid = user.get("user_id") or user.get("id")
    if meta.get("user_id") is not None and uid is not None:
        try:
            if int(meta["user_id"]) == int(uid):
                return True
        except (TypeError, ValueError):
            pass
    # Project membership check (best-effort)
    project_slug = meta.get("project_slug")
    if project_slug:
        try:
            from app.auth import check_project_permission  # type: ignore
            if check_project_permission(user, project_slug, required_role="viewer"):
                return True
        except Exception:
            pass
    # Globally-scoped artifact (no project) → allow any authed user
    if not project_slug and meta.get("user_id") is None:
        return True
    return False


@router.get("/")
def list_artifacts(
    request: Request,
    project: Optional[str] = Query(None),
    run_id: Optional[str] = Query(None),
    kind: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    _require_user(request)
    items = art.list_artifacts(
        project=project, run_id=run_id, kind=kind,
        limit=limit, offset=offset,
    )
    total = art.count_artifacts(project=project, run_id=run_id, kind=kind)
    return {"ok": True, "artifacts": items, "total": total,
            "limit": limit, "offset": offset}


@router.get("/{artifact_id}")
def get_artifact_meta(artifact_id: str, request: Request):
    user = _require_user(request)
    meta = art._get_meta(artifact_id)
    if not meta:
        raise HTTPException(404, "artifact not found")
    if not _can_access(meta, user):
        raise HTTPException(403, "forbidden")
    # Strip server-internal fields
    out = {
        "id": meta["id"],
        "name": meta["filename"],
        "kind": meta.get("kind") or meta.get("file_type") or "other",
        "size_bytes": meta.get("size_bytes"),
        "project_slug": meta.get("project_slug"),
        "run_id": meta.get("run_id"),
        "agent_name": meta.get("agent_name"),
        "metadata": meta.get("metadata"),
        "created_at": str(meta.get("created_at")) if meta.get("created_at") else None,
        "mime": art.mime_for(meta.get("kind") or "", meta["filename"]),
    }
    return {"ok": True, "artifact": out}


@router.get("/{artifact_id}/download")
def download_artifact(artifact_id: str, request: Request):
    user = _require_user(request)
    meta = art._get_meta(artifact_id)
    if not meta:
        raise HTTPException(404, "artifact not found")
    if not _can_access(meta, user):
        raise HTTPException(403, "forbidden")
    payload = art.get_artifact(artifact_id)
    if not payload or not payload.get("content_bytes"):
        raise HTTPException(410, "artifact file missing on disk")
    content = payload["content_bytes"]
    filename = payload["name"] or f"{artifact_id}.bin"
    mime = payload["mime"]

    def _iter():
        buf = io.BytesIO(content)
        while True:
            chunk = buf.read(64 * 1024)
            if not chunk:
                break
            yield chunk

    return StreamingResponse(
        _iter(),
        media_type=mime,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(content)),
        },
    )


@router.get("/{artifact_id}/thumbnail")
def get_thumbnail(artifact_id: str, request: Request):
    user = _require_user(request)
    meta = art._get_meta(artifact_id)
    if not meta:
        raise HTTPException(404, "artifact not found")
    if not _can_access(meta, user):
        raise HTTPException(403, "forbidden")
    thumb = art.get_thumbnail(artifact_id)
    if thumb is None:
        raise HTTPException(404, "no thumbnail")
    return Response(content=thumb, media_type="image/png")


@router.delete("/{artifact_id}")
def delete_artifact(artifact_id: str, request: Request):
    user = _require_user(request)
    meta = art._get_meta(artifact_id)
    if not meta:
        raise HTTPException(404, "artifact not found")
    if not _can_access(meta, user):
        raise HTTPException(403, "forbidden")
    ok = art.delete_artifact(artifact_id, hard=False)
    return {"ok": ok, "id": artifact_id}
