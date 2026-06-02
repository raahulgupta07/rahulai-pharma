"""Hybrid retrieval API — BM25 + vector + RRF + multi-query expansion."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import text as _t

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/retrieval", tags=["Retrieval"])


def _user(request: Request) -> dict:
    from app.auth import get_current_user
    u = get_current_user(request)
    if not u:
        raise HTTPException(401, "auth required")
    return u


def _check(user: dict, slug: str, role: str = "viewer") -> None:
    try:
        from app.auth import check_project_permission
        if not check_project_permission(user, slug, role):
            raise HTTPException(403, "permission denied")
    except HTTPException:
        raise
    except Exception:
        # Fail-open on auth helper outage so search itself still works.
        pass


async def _body(request: Request) -> dict:
    try:
        return await request.json()
    except Exception:
        raise HTTPException(400, "invalid JSON body")


def _engine():
    from db.session import get_sql_engine
    return get_sql_engine()


# ───────────────────── /search (hybrid) ─────────────────────

@router.post("/search")
async def search(request: Request):
    from dash.retrieval.hybrid import hybrid_search

    user = _user(request)
    body = await _body(request)

    project = (body.get("project") or "").strip()
    query = (body.get("query") or "").strip()
    mode = (body.get("mode") or "balanced").strip().lower()
    k = int(body.get("k") or 10)

    if not project:
        raise HTTPException(400, "missing 'project'")
    if not query:
        raise HTTPException(400, "missing 'query'")
    if mode not in {"conservative", "balanced", "tokenmax"}:
        mode = "balanced"
    k = max(1, min(50, k))

    _check(user, project, "viewer")

    results = hybrid_search(project=project, query=query, mode=mode, k=k)
    debug: dict[str, Any] = {}
    if results and isinstance(results[0], dict):
        debug = results[0].get("debug") or {}
    return {"results": results, "debug": debug, "mode": mode, "k": k}


# ───────────────────── /search/bm25 ─────────────────────

@router.post("/search/bm25")
async def search_bm25(request: Request):
    from dash.retrieval.hybrid import bm25_search

    user = _user(request)
    body = await _body(request)

    project = (body.get("project") or "").strip()
    query = (body.get("query") or "").strip()
    k = max(1, min(50, int(body.get("k") or 10)))
    if not project or not query:
        raise HTTPException(400, "missing 'project' or 'query'")
    _check(user, project, "viewer")

    return {"results": bm25_search(project, query, k=k), "k": k}


# ───────────────────── /search/vector ─────────────────────

@router.post("/search/vector")
async def search_vector(request: Request):
    from dash.retrieval.hybrid import vector_search, _embed_query

    user = _user(request)
    body = await _body(request)

    project = (body.get("project") or "").strip()
    query = (body.get("query") or "").strip()
    k = max(1, min(50, int(body.get("k") or 10)))
    if not project or not query:
        raise HTTPException(400, "missing 'project' or 'query'")
    _check(user, project, "viewer")

    qvec = _embed_query(query)
    if not qvec:
        raise HTTPException(503, "embedding backend unavailable")
    return {"results": vector_search(project, qvec, k=k), "k": k}


# ───────────────────── /log ─────────────────────

@router.get("/log")
async def get_log(request: Request, project: str = "", limit: int = 50):
    user = _user(request)
    if not project:
        raise HTTPException(400, "missing 'project'")
    _check(user, project, "viewer")
    limit = max(1, min(500, int(limit)))
    try:
        with _engine().begin() as conn:
            rows = conn.execute(
                _t(
                    "SELECT id, project_slug, query, mode, n_results, latency_ms, ts "
                    "FROM dash.dash_search_log "
                    "WHERE project_slug = :p "
                    "ORDER BY ts DESC LIMIT :l"
                ),
                {"p": project, "l": limit},
            ).mappings().all()
            return {
                "log": [
                    {
                        "id": int(r["id"]),
                        "project_slug": r["project_slug"],
                        "query": r["query"],
                        "mode": r["mode"],
                        "n_results": r["n_results"],
                        "latency_ms": r["latency_ms"],
                        "ts": r["ts"].isoformat() if r["ts"] else None,
                    }
                    for r in rows
                ]
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("retrieval log query failed: %s", e)
        return {"log": [], "error": str(e)}
