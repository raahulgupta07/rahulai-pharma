"""
Quality Scores API
==================

Retrieve quality scores and stats for a project.
"""

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import create_engine as _sa_create_engine, text
from sqlalchemy.pool import NullPool

from db import db_url

router = APIRouter(prefix="/api/projects", tags=["Scores"])
_engine = _sa_create_engine(db_url, poolclass=NullPool)


def _get_user(request: Request) -> dict:
    user = getattr(getattr(request, 'state', None), 'user', None)
    if not user:
        raise HTTPException(401, "Not authenticated")
    return user


@router.get("/{slug}/scores")
def list_scores(slug: str, request: Request):
    """List recent quality scores for a project."""
    _get_user(request)
    with _engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, session_id, score, reasoning, created_at "
            "FROM public.dash_quality_scores WHERE project_slug = :slug "
            "ORDER BY created_at DESC LIMIT 50"
        ), {"slug": slug}).fetchall()

    scores = [
        {"id": r[0], "session_id": r[1], "score": r[2], "reasoning": r[3],
         "created_at": str(r[4]) if r[4] else None}
        for r in rows
    ]
    return {"scores": scores}


@router.get("/{slug}/scores/latest")
def latest_score(slug: str, session_id: str, request: Request):
    """Get the latest quality score for a specific session."""
    _get_user(request)
    with _engine.connect() as conn:
        row = conn.execute(text(
            "SELECT score, reasoning FROM public.dash_quality_scores "
            "WHERE project_slug = :slug AND session_id = :sid "
            "ORDER BY created_at DESC LIMIT 1"
        ), {"slug": slug, "sid": session_id}).fetchone()

    if row:
        return {"score": row[0], "reasoning": row[1]}
    return {"score": None, "reasoning": None}


@router.get("/{slug}/scores/stats")
def score_stats(slug: str, request: Request):
    """Get average score and distribution for a project."""
    _get_user(request)
    with _engine.connect() as conn:
        avg = conn.execute(text(
            "SELECT AVG(score)::numeric(3,1), COUNT(*) FROM public.dash_quality_scores WHERE project_slug = :slug"
        ), {"slug": slug}).fetchone()

        dist = conn.execute(text(
            "SELECT score, COUNT(*) FROM public.dash_quality_scores WHERE project_slug = :slug GROUP BY score ORDER BY score"
        ), {"slug": slug}).fetchall()

    return {
        "average": float(avg[0]) if avg and avg[0] else None,
        "total": avg[1] if avg else 0,
        "distribution": {r[0]: r[1] for r in dist},
    }
