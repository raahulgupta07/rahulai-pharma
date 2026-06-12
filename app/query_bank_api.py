"""Admin API for the query bank (continuous query learning, P3).

Super-admin gated (mirrors app/cache_curator_api.py). Surfaces the captured
chat patterns, the review gate (approve/reject/promote/demote), the curator
run, and the shadow-measurement stats.

  GET  /api/projects/{slug}/query-bank/stats            — counts + shadow repeat-rate
  GET  /api/projects/{slug}/query-bank/patterns         — list (filter ?status=)
  POST /api/projects/{slug}/query-bank/{id}/approve     — pending -> candidate
  POST /api/projects/{slug}/query-bank/{id}/reject      — -> demoted
  POST /api/projects/{slug}/query-bank/{id}/promote     — verify + -> proven
  POST /api/projects/{slug}/query-bank/{id}/demote      — -> demoted
  POST /api/projects/{slug}/query-bank/curate           — run curator (dry_run default true)
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, Request

logger = logging.getLogger(__name__)

router = APIRouter(tags=["query-bank"])


def _get_user(request: Request) -> dict:
    user = getattr(getattr(request, "state", None), "user", None)
    if not user:
        raise HTTPException(401, "Not authenticated")
    return user


def _gate(request: Request):
    user = _get_user(request)
    if not user.get("is_super") and not user.get("is_super_admin"):
        raise HTTPException(403, "super-admin only")


@router.get("/api/projects/{slug}/query-bank/stats")
async def stats(slug: str, request: Request):
    _gate(request)
    from dash.learning.query_curator import bank_stats
    return bank_stats(slug)


@router.get("/api/projects/{slug}/query-bank/patterns")
async def patterns(slug: str, request: Request,
                   status: str | None = Query(None),
                   source: str = Query("chat"),
                   limit: int = Query(200, le=1000)):
    _gate(request)
    from dash.learning.query_curator import list_patterns
    return {"patterns": list_patterns(slug, source=source, status=status, limit=limit)}


@router.post("/api/projects/{slug}/query-bank/{pattern_id}/approve")
async def approve(slug: str, pattern_id: int, request: Request):
    _gate(request)
    from dash.learning.query_curator import approve_pattern
    return approve_pattern(slug, pattern_id)


@router.post("/api/projects/{slug}/query-bank/{pattern_id}/reject")
async def reject(slug: str, pattern_id: int, request: Request):
    _gate(request)
    from dash.learning.query_curator import reject_pattern
    return reject_pattern(slug, pattern_id)


@router.post("/api/projects/{slug}/query-bank/{pattern_id}/promote")
async def promote(slug: str, pattern_id: int, request: Request):
    _gate(request)
    from dash.learning.query_curator import promote_pattern
    return promote_pattern(slug, pattern_id)


@router.post("/api/projects/{slug}/query-bank/{pattern_id}/demote")
async def demote(slug: str, pattern_id: int, request: Request):
    _gate(request)
    from dash.learning.query_curator import demote_pattern
    return demote_pattern(slug, pattern_id)


@router.post("/api/projects/{slug}/query-bank/curate")
async def curate(slug: str, request: Request,
                 dry_run: bool = Query(True),
                 limit: int = Query(50, le=500)):
    _gate(request)
    from dash.learning.query_curator import run_query_curator, demote_on_negative_feedback
    neg = demote_on_negative_feedback(slug)
    res = run_query_curator(slug, limit=limit, dry_run=dry_run)
    res["negative_feedback_demoted"] = neg.get("demoted", 0)
    return res


@router.post("/api/projects/{slug}/query-bank/generalize")
async def generalize(slug: str, request: Request,
                     dry_run: bool = Query(True),
                     max_clusters: int = Query(10, le=50)):
    """P5: cluster proven learned queries → propose ONE parameterized template
    per family (review-gated as pending). dry_run=True returns proposals only."""
    _gate(request)
    from dash.learning.query_generalize import propose_generalizations
    return propose_generalizations(slug, max_clusters=max_clusters, dry_run=dry_run)


@router.post("/api/projects/{slug}/query-bank/fold-training")
async def fold_training(slug: str, request: Request):
    """P6: copy proven learned queries into the training corpus now (also runs
    automatically after every retrain)."""
    _gate(request)
    from dash.learning.query_curator import fold_proven_into_training
    return fold_proven_into_training(slug)
