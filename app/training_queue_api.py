"""
Training queue API — Option B (in-process workers + Redis).

Endpoints:
  POST /api/projects/{slug}/retrain-queued
       body (optional): {"tables": [...], "steps": [...]}
       → 202 {"run_id", "jobs_enqueued", "queue_depth"}

  GET  /api/projects/{slug}/training-runs/{run_id}/status-v2
       → get_run_status(run_id) rollup

Auth: editor (POST), viewer (GET). Kill switch: TRAINING_QUEUE_DISABLED=1
returns 503 on POST. Fail-soft: never raises 500 unhandled.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import text

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/projects", tags=["training-queue"])


class RetrainQueuedBody(BaseModel):
    tables: Optional[list[str]] = None
    steps: Optional[list[str]] = None


def _disabled() -> bool:
    return os.getenv("TRAINING_QUEUE_DISABLED", "").lower() in ("1", "true", "yes")


def _list_project_tables(slug: str) -> list[str]:
    """List user tables in the project schema (best-effort)."""
    try:
        from db.session import get_write_engine
        eng = get_write_engine()
        with eng.begin() as conn:
            rows = conn.execute(text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = :s AND table_type = 'BASE TABLE' "
                "ORDER BY table_name"
            ), {"s": slug}).fetchall()
            return [r[0] for r in rows]
    except Exception as e:
        logger.warning(f"training_queue_api: list tables failed for {slug}: {e}")
        return []


def _redis_depth() -> int:
    try:
        from dash.training.train_queue import _get_redis, _REDIS_KEY
        r = _get_redis()
        if r is None:
            return -1
        return int(r.llen(_REDIS_KEY))
    except Exception:
        return -1


@router.post("/{slug}/retrain-queued", status_code=202)
async def retrain_queued(slug: str, body: RetrainQueuedBody, request: Request):
    """Enqueue training jobs for the project. Editor role required."""
    if _disabled():
        raise HTTPException(503, "training queue disabled (TRAINING_QUEUE_DISABLED=1)")

    # Auth
    try:
        from app.auth import get_current_user, check_project_permission
        user = get_current_user(request)
        if not user:
            raise HTTPException(401, "auth required")
        proj = check_project_permission(user, slug, required_role="editor")
        if not proj:
            raise HTTPException(403, "editor role required")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("training_queue_api: auth check failed")
        raise HTTPException(500, f"auth error: {e}")

    tables = body.tables or _list_project_tables(slug)
    if not tables:
        return {
            "ok": False,
            "error": "no tables to train",
            "run_id": None,
            "jobs_enqueued": 0,
            "queue_depth": _redis_depth(),
        }

    steps = body.steps or ["profile_v2"]
    try:
        from dash.training.train_queue import (
            create_training_run,
            enqueue_train_jobs,
        )
        run_id = create_training_run(slug, len(tables))
        n = enqueue_train_jobs(slug, tables, run_id, steps=steps)
        return {
            "ok": True,
            "run_id": run_id,
            "jobs_enqueued": n,
            "queue_depth": _redis_depth(),
            "tables": tables,
            "steps": steps,
        }
    except Exception as e:
        logger.exception("training_queue_api: enqueue failed")
        return {"ok": False, "error": str(e), "jobs_enqueued": 0}


@router.get("/{slug}/training-runs/{run_id}/status-v2")
async def status_v2(slug: str, run_id: int, request: Request):
    """Aggregate run status from dash_training_jobs. Viewer role required."""
    try:
        from app.auth import get_current_user, check_project_permission
        user = get_current_user(request)
        if not user:
            raise HTTPException(401, "auth required")
        proj = check_project_permission(user, slug, required_role="viewer")
        if not proj:
            raise HTTPException(403, "viewer role required")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("training_queue_api: auth check failed")
        raise HTTPException(500, f"auth error: {e}")

    try:
        from dash.training.train_queue import get_run_status
        return get_run_status(run_id)
    except Exception as e:
        logger.exception("training_queue_api: get_run_status failed")
        return {"ok": False, "error": str(e), "run_id": run_id}
