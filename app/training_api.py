"""Training Steps API — read-only.

Exposes V2 per-step training progress so the frontend can render a live
35-row view instead of a single frozen `current_step`.

Endpoints under /api/projects/{slug}:
  GET /training-steps   per-step rows for a run (latest run if run_id omitted)
  GET /training-runs    recent runs with durations

Mirrors app/learning.py auth + engine pattern (_get_user / check_project_permission
+ module-level NullPool engine). Fail-soft: returns empty lists / nulls on any
error, never 500 on a missing table.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import create_engine as _sa_create_engine, text
from sqlalchemy.pool import NullPool

from db import db_url

router = APIRouter(prefix="/api/projects", tags=["Training"])
_engine = _sa_create_engine(db_url, poolclass=NullPool)
logger = logging.getLogger(__name__)


def _get_user(request: Request) -> dict:
    user = getattr(getattr(request, "state", None), "user", None)
    if not user:
        raise HTTPException(401, "Not authenticated")
    return user


def _check_access(user: dict, slug: str):
    """Verify user has access to project (owner, shared, or super admin)."""
    from app.auth import check_project_permission
    perm = check_project_permission(user, slug)
    if not perm:
        raise HTTPException(403, "Access denied")


def _iso(v):
    return v.isoformat() if v is not None and hasattr(v, "isoformat") else (str(v) if v is not None else None)


@router.get("/{slug}/training-steps")
def training_steps(slug: str, request: Request, run_id: Optional[int] = None):
    """Per-step training progress (35-row view).

    If run_id omitted, uses the latest run for the slug. Returns ALL step rows
    for that run_id; if those are empty (cache rows are upserted per-project and
    may carry a different/null run_id), falls back to the project's step rows so
    the view is never empty.
    """
    user = _get_user(request)
    _check_access(user, slug)

    resolved_run_id = run_id
    run_status = None
    steps: list[dict] = []

    try:
        with _engine.connect() as conn:
            # Resolve latest run if needed.
            if resolved_run_id is None:
                row = conn.execute(text(
                    "SELECT id FROM public.dash_training_runs "
                    "WHERE project_slug = :s ORDER BY started_at DESC LIMIT 1"
                ), {"s": slug}).fetchone()
                if row:
                    resolved_run_id = row[0]

            # Fetch run status for context (best-effort).
            if resolved_run_id is not None:
                rs = conn.execute(text(
                    "SELECT status FROM public.dash_training_runs WHERE id = :r"
                ), {"r": resolved_run_id}).fetchone()
                if rs:
                    run_status = rs[0]

            step_sql = (
                "SELECT step_no, name, scope, status, elapsed_ms, fp, error, "
                "started_at, finished_at "
                "FROM public.dash_training_steps WHERE {where} "
                "ORDER BY step_no NULLS LAST, name"
            )

            rows = []
            if resolved_run_id is not None:
                rows = conn.execute(
                    text(step_sql.format(where="run_id = :r")),
                    {"r": resolved_run_id},
                ).fetchall()

            # Fall back to project-scoped step rows when the run has no step rows
            # (cache rows are upserted per project, possibly with a null/other run_id).
            if not rows:
                rows = conn.execute(
                    text(step_sql.format(where="project_slug = :s")),
                    {"s": slug},
                ).fetchall()

            steps = [{
                "step_no": r[0],
                "name": r[1],
                "scope": r[2],
                "status": r[3],
                "elapsed_ms": r[4],
                "fp": r[5],
                "error": r[6],
                "started_at": _iso(r[7]),
                "finished_at": _iso(r[8]),
            } for r in rows]
    except Exception as e:  # noqa: BLE001
        logger.warning(f"training-steps query failed for {slug}: {e}")
        return {"run_id": resolved_run_id, "run_status": run_status, "steps": []}

    return {"run_id": resolved_run_id, "run_status": run_status, "steps": steps}


# NOTE: GET /{slug}/training-runs lives in app/learning.py (richer shape:
# tables/steps/logs — consumed by the Settings training-progress UI). The
# duplicate handler that used to be here was removed to avoid a shadowed route.
