"""Deterministic auto-linker API (zero LLM).

Endpoints under ``/api/linker``:
- POST   /extract                     — return entities + links, no write
- POST   /link                        — extract AND write, returns counts
- GET    /entities                    — list entities (project, kind, search filters)
- GET    /entities/{id}/links         — in/out edges for one entity
- POST   /relink-all                  — backfill entry point
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/linker", tags=["linker"])


def _get_user(request: Request) -> dict:
    user = getattr(getattr(request, "state", None), "user", None)
    if not user:
        try:
            from app.auth import get_current_user
            user = get_current_user(request)
        except Exception:
            user = None
    return user or {"id": None, "username": None}


def _engine():
    from db.session import get_sql_engine
    return get_sql_engine()


# ── Request models ─────────────────────────────────────────────────────────

class ExtractBody(BaseModel):
    project: str
    text: str
    page_kind: Optional[str] = None


class LinkBody(BaseModel):
    project: str
    text: str
    page_kind: Optional[str] = None
    source_ref: Optional[str] = None


# ── Endpoints ──────────────────────────────────────────────────────────────

@router.post("/extract")
def extract(body: ExtractBody, request: Request):
    """Run the deterministic extractor without writing to the DB."""
    _get_user(request)
    from dash.linker.extractor import extract_entities, extract_links

    source_id: Optional[int] = None
    entities = extract_entities(body.text or "", page_kind=body.page_kind)
    links = extract_links(body.text or "", page_kind=body.page_kind, source_id=source_id)
    return {
        "ok": True,
        "project": body.project,
        "entities": entities,
        "links": links,
        "counts": {"entities": len(entities), "links": len(links)},
    }


@router.post("/link")
def link(body: LinkBody, request: Request):
    """Extract entities + links and persist via upsert."""
    _get_user(request)
    from dash.linker.extractor import link_text

    result = link_text(
        project=body.project,
        text=body.text or "",
        page_kind=body.page_kind,
        source_ref=body.source_ref,
    )
    return {"ok": True, **result}


@router.get("/entities")
def list_entities(
    request: Request,
    project: str = Query(...),
    kind: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
):
    """List entities with link counts."""
    _get_user(request)
    from sqlalchemy import text as sa_text

    eng = _engine()
    if eng is None:
        raise HTTPException(500, "no_engine")

    sql = """
        SELECT e.id, e.kind, e.name, e.name_normalized, e.created_at,
               COALESCE((
                 SELECT COUNT(*) FROM dash.dash_entity_links l
                 WHERE l.src_entity_id = e.id OR l.dst_entity_id = e.id
               ), 0) AS link_count
        FROM dash.dash_entities e
        WHERE e.project_slug = :p
          AND (CAST(:k AS TEXT) IS NULL OR e.kind = :k)
          AND (CAST(:q AS TEXT) IS NULL OR e.name_normalized LIKE :qlike)
        ORDER BY link_count DESC, e.id DESC
        LIMIT :lim
    """
    qlike = f"%{search.lower()}%" if search else None
    with eng.connect() as conn:
        rows = conn.execute(
            sa_text(sql),
            {"p": project, "k": kind, "q": search, "qlike": qlike, "lim": limit},
        ).fetchall()

    return {
        "ok": True,
        "entities": [
            {
                "id": r[0],
                "kind": r[1],
                "name": r[2],
                "name_normalized": r[3],
                "created_at": r[4].isoformat() if r[4] else None,
                "link_count": int(r[5] or 0),
            }
            for r in rows
        ],
        "count": len(rows),
    }


@router.get("/entities/{entity_id}/links")
def entity_links(entity_id: int, request: Request):
    """Return all in/out edges for a single entity."""
    _get_user(request)
    from sqlalchemy import text as sa_text

    eng = _engine()
    if eng is None:
        raise HTTPException(500, "no_engine")

    with eng.connect() as conn:
        row = conn.execute(
            sa_text("SELECT id, project_slug, kind, name FROM dash.dash_entities WHERE id = :i"),
            {"i": entity_id},
        ).fetchone()
        if not row:
            raise HTTPException(404, "entity not found")
        entity = {"id": row[0], "project_slug": row[1], "kind": row[2], "name": row[3]}

        out_rows = conn.execute(
            sa_text("""
                SELECT l.id, l.rel, l.confidence, l.source_ref, l.created_at,
                       d.id, d.kind, d.name
                FROM dash.dash_entity_links l
                JOIN dash.dash_entities d ON d.id = l.dst_entity_id
                WHERE l.src_entity_id = :i
                ORDER BY l.id DESC
                LIMIT 500
            """),
            {"i": entity_id},
        ).fetchall()

        in_rows = conn.execute(
            sa_text("""
                SELECT l.id, l.rel, l.confidence, l.source_ref, l.created_at,
                       s.id, s.kind, s.name
                FROM dash.dash_entity_links l
                JOIN dash.dash_entities s ON s.id = l.src_entity_id
                WHERE l.dst_entity_id = :i
                ORDER BY l.id DESC
                LIMIT 500
            """),
            {"i": entity_id},
        ).fetchall()

    def _pack(r):
        return {
            "id": r[0],
            "rel": r[1],
            "confidence": float(r[2] or 0.0),
            "source_ref": r[3],
            "created_at": r[4].isoformat() if r[4] else None,
            "other": {"id": r[5], "kind": r[6], "name": r[7]},
        }

    return {
        "ok": True,
        "entity": entity,
        "out": [_pack(r) for r in out_rows],
        "in": [_pack(r) for r in in_rows],
        "count": len(out_rows) + len(in_rows),
    }


@router.post("/relink-all")
def relink_all(project: str = Query(...), limit: int = Query(1000, ge=1, le=10000)):
    """Kicks the backfill loop."""
    from dash.linker.runner import relink_all as _relink_all
    return {"ok": True, **_relink_all(project=project, limit=limit)}
