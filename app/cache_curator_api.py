"""Admin API for the leader-driven answer-cache curator. [P3]

Endpoints (super-admin gated, mirrors app/usage_api.py auth):
  POST /api/projects/{slug}/cache/curate   — run the curator (dry_run default True)
  GET  /api/projects/{slug}/cache/stats     — cache inspection counts
  GET  /api/projects/{slug}/cache/clusters  — frequent-question clusters

The parent wires this router into main.py (`include_router`). This module only
defines `router` + handlers. Fail-soft: handlers return a JSON error dict rather
than a 500 where reasonable (auth errors still raise HTTPException so the gate
behaves like every other admin endpoint).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, Request

logger = logging.getLogger(__name__)

router = APIRouter(tags=["cache-curator"])


# --- auth (mirrors app/usage_api.py:_get_user / _gate, lines 32-42) -----------
def _get_user(request: Request) -> dict:
    user = getattr(getattr(request, "state", None), "user", None)
    if not user:
        raise HTTPException(401, "Not authenticated")
    return user


def _gate(request: Request):
    user = _get_user(request)
    if not user.get("is_super") and not user.get("is_super_admin"):
        raise HTTPException(403, "super-admin only")


@router.post("/api/projects/{slug}/cache/curate")
async def curate(
    slug: str,
    request: Request,
    dry_run: bool = Query(True),
    max_promote: int = Query(10),
):
    """Run the leader cache curator. dry_run defaults True so a click never
    auto-writes unless the caller explicitly asks (dry_run=false)."""
    _gate(request)
    try:
        from dash.learning.cache_curator import run_curator
        return await run_curator(slug, dry_run=dry_run, max_promote=max_promote)
    except Exception as exc:  # noqa: BLE001
        logger.warning("cache curate failed for %s: %s", slug, exc)
        return {"ok": False, "error": str(exc),
                "candidates": [], "promoted": [], "skipped": [], "dry_run": dry_run}


@router.get("/api/projects/{slug}/cache/stats")
def stats(slug: str, request: Request):
    """Read-only cache inspection counts (by status + promoted_by + total hits)."""
    _gate(request)
    try:
        from dash.learning.cache_curator import curator_stats
        return curator_stats(slug)
    except Exception as exc:  # noqa: BLE001
        logger.warning("cache stats failed for %s: %s", slug, exc)
        return {"ok": False, "error": str(exc),
                "by_status": {}, "by_promoted_by": {}, "total_hit_count": 0, "total": 0}


@router.get("/api/projects/{slug}/cache/clusters")
def clusters(
    slug: str,
    request: Request,
    days: int = Query(30),
    min_count: int = Query(2),
    limit: int = Query(50),
):
    """Frequent-question clusters (so the admin can see what's worth caching)."""
    _gate(request)
    try:
        from dash.learning.question_clusters import cluster_questions  # lazy
        return {"clusters": cluster_questions(slug, days=days, min_count=min_count, limit=limit)}
    except Exception as exc:  # noqa: BLE001
        logger.warning("cache clusters failed for %s: %s", slug, exc)
        return {"ok": False, "error": str(exc), "clusters": []}
