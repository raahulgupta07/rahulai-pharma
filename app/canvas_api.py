"""Canvas API — Obsidian-style free-form canvas boards per project.

Endpoints (prefix ``/api/canvas``):

* ``GET    /api/canvas/{slug}/list``        list canvases for project (newest first)
* ``GET    /api/canvas/{slug}/{id}``        fetch single canvas
* ``POST   /api/canvas/{slug}``             create canvas  body: {name, board}
* ``PUT    /api/canvas/{slug}/{id}``        update canvas  body: {name?, board?}
* ``DELETE /api/canvas/{slug}/{id}``        delete canvas

Storage: ``dash.dash_canvas`` (see migration 153).
Board shape: ``{"cards": [{id, type, x, y, w, h, content}]}``.

Style matches ``app/golden_api.py`` / ``app/actions_api.py``.
Writes via ``get_write_engine()``; JSONB via ``CAST(:b AS jsonb)``.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import text

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/canvas", tags=["canvas"])


# ── Auth ──────────────────────────────────────────────────────────────────


def _get_user(request: Request) -> Dict[str, Any]:
    user = getattr(getattr(request, "state", None), "user", None)
    if not user:
        try:
            from app.auth import get_current_user  # type: ignore
            user = get_current_user(request)
        except Exception:
            user = None
    if not user:
        raise HTTPException(401, "Not authenticated")
    return user


def _username(user: Dict[str, Any]) -> str:
    return (
        user.get("username")
        or user.get("name")
        or user.get("email")
        or "user"
    )


# ── Engines ───────────────────────────────────────────────────────────────


def _read_engine():
    from db.session import get_sql_engine  # type: ignore
    return get_sql_engine()


def _write_engine():
    try:
        from db.session import get_write_engine  # type: ignore
        return get_write_engine()
    except Exception:
        from db.session import get_sql_engine  # type: ignore
        return get_sql_engine()


# ── Models ────────────────────────────────────────────────────────────────


class CanvasCreate(BaseModel):
    name: Optional[str] = None
    board: Optional[Dict[str, Any]] = None


class CanvasUpdate(BaseModel):
    name: Optional[str] = None
    board: Optional[Dict[str, Any]] = None


def _row_to_dict(row) -> Dict[str, Any]:
    board = row.board
    if isinstance(board, str):
        try:
            board = json.loads(board)
        except Exception:
            board = {"cards": []}
    return {
        "id": str(row.id),
        "project_slug": row.project_slug,
        "name": row.name,
        "board": board or {"cards": []},
        "created_by": row.created_by,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


# ── Endpoints ─────────────────────────────────────────────────────────────


@router.get("/{slug}/list")
def list_canvases(slug: str, request: Request) -> Dict[str, Any]:
    _get_user(request)
    try:
        eng = _read_engine()
        with eng.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT id, project_slug, name, board, created_by, "
                    "updated_at, created_at "
                    "FROM dash.dash_canvas "
                    "WHERE project_slug = :s "
                    "ORDER BY updated_at DESC"
                ),
                {"s": slug},
            ).fetchall()
        return {"canvases": [_row_to_dict(r) for r in rows]}
    except Exception as e:
        logger.exception("list_canvases failed for %s: %s", slug, e)
        raise HTTPException(503, f"canvas storage unavailable: {e}")


@router.get("/{slug}/{canvas_id}")
def get_canvas(slug: str, canvas_id: str, request: Request) -> Dict[str, Any]:
    _get_user(request)
    try:
        eng = _read_engine()
        with eng.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT id, project_slug, name, board, created_by, "
                    "updated_at, created_at "
                    "FROM dash.dash_canvas "
                    "WHERE project_slug = :s AND id = :i"
                ),
                {"s": slug, "i": canvas_id},
            ).fetchone()
        if not row:
            raise HTTPException(404, f"canvas {canvas_id} not found")
        return _row_to_dict(row)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("get_canvas failed: %s", e)
        raise HTTPException(503, f"canvas storage unavailable: {e}")


@router.post("/{slug}")
def create_canvas(slug: str, body: CanvasCreate, request: Request) -> Dict[str, Any]:
    user = _get_user(request)
    name = (body.name or "Untitled canvas").strip() or "Untitled canvas"
    board = body.board if isinstance(body.board, dict) else {"cards": []}
    if "cards" not in board or not isinstance(board.get("cards"), list):
        board = {"cards": []}
    try:
        eng = _write_engine()
        with eng.begin() as conn:
            row = conn.execute(
                text(
                    "INSERT INTO dash.dash_canvas "
                    "(project_slug, name, board, created_by) "
                    "VALUES (:s, :n, CAST(:b AS jsonb), :u) "
                    "RETURNING id, project_slug, name, board, "
                    "created_by, updated_at, created_at"
                ),
                {
                    "s": slug,
                    "n": name,
                    "b": json.dumps(board),
                    "u": _username(user),
                },
            ).fetchone()
        return _row_to_dict(row)
    except Exception as e:
        logger.exception("create_canvas failed: %s", e)
        raise HTTPException(500, f"canvas create failed: {e}")


@router.put("/{slug}/{canvas_id}")
def update_canvas(
    slug: str, canvas_id: str, body: CanvasUpdate, request: Request
) -> Dict[str, Any]:
    _get_user(request)
    sets: list[str] = []
    params: Dict[str, Any] = {"s": slug, "i": canvas_id}
    if body.name is not None:
        n = body.name.strip()
        if not n:
            raise HTTPException(400, "name cannot be empty")
        sets.append("name = :n")
        params["n"] = n
    if body.board is not None:
        board = body.board if isinstance(body.board, dict) else {"cards": []}
        if "cards" not in board or not isinstance(board.get("cards"), list):
            board = {"cards": []}
        sets.append("board = CAST(:b AS jsonb)")
        params["b"] = json.dumps(board)
    if not sets:
        raise HTTPException(400, "no fields to update")
    sets.append("updated_at = now()")
    sql = (
        "UPDATE dash.dash_canvas SET "
        + ", ".join(sets)
        + " WHERE project_slug = :s AND id = :i "
        "RETURNING id, project_slug, name, board, created_by, updated_at, created_at"
    )
    try:
        eng = _write_engine()
        with eng.begin() as conn:
            row = conn.execute(text(sql), params).fetchone()
        if not row:
            raise HTTPException(404, f"canvas {canvas_id} not found")
        return _row_to_dict(row)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("update_canvas failed: %s", e)
        raise HTTPException(500, f"canvas update failed: {e}")


@router.delete("/{slug}/{canvas_id}")
def delete_canvas(slug: str, canvas_id: str, request: Request) -> Dict[str, Any]:
    _get_user(request)
    try:
        eng = _write_engine()
        with eng.begin() as conn:
            res = conn.execute(
                text(
                    "DELETE FROM dash.dash_canvas "
                    "WHERE project_slug = :s AND id = :i"
                ),
                {"s": slug, "i": canvas_id},
            )
        if res.rowcount == 0:
            raise HTTPException(404, f"canvas {canvas_id} not found")
        return {"ok": True, "id": canvas_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("delete_canvas failed: %s", e)
        raise HTTPException(500, f"canvas delete failed: {e}")
