"""Deep Deck API — SSE-streamed 6-stage research-then-present pipeline.

POST /api/slides/deep-build   - stream pipeline events (text/event-stream)
GET  /api/slides/deep-runs    - list past runs
GET  /api/slides/deep-runs/{id} - run detail w/ gaps + queries
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from dash.utils import safe_dumps

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/slides", tags=["deep_deck"])


def _get_user(request: Request) -> Dict[str, Any]:
    try:
        from app.export import _get_user as _u
        return _u(request)
    except Exception:
        from app.auth import validate_token
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return validate_token(auth[7:]) or {}
        return {}


def _engine():
    try:
        from db.session import get_sql_engine
        return get_sql_engine()
    except Exception:
        try:
            from db import get_sql_engine  # type: ignore
            return get_sql_engine()
        except Exception:
            return None


@router.post("/deep-build")
async def deep_build(request: Request) -> StreamingResponse:
    """SSE stream of 6-stage deep-deck pipeline.

    Body:
        project_slug: str
        agent_name: str
        messages: [{role, content}, ...]   (recent chat history)
        session_id: int (optional)
        config: {ml_augment, web_benchmark, counter_hypothesis, ...} (optional)

    Stream events (newline-delimited JSON, prefixed `data: `):
        {stage, status, message, data, ts}
    """
    user = _get_user(request)
    if not user:
        raise HTTPException(401, "auth_required")

    auth_hdr = request.headers.get("Authorization", "")
    auth_token = auth_hdr[7:] if auth_hdr.startswith("Bearer ") else None

    body = await request.json()
    project_slug = body.get("project_slug") or ""
    agent_name = body.get("agent_name") or "Agent"
    messages = body.get("messages") or []
    session_id = body.get("session_id")
    config = body.get("config") or {}

    if not project_slug:
        raise HTTPException(400, "project_slug required")
    if not messages:
        raise HTTPException(400, "messages required (chat history)")

    try:
        from dash.tools.deep_deck import orchestrate_deep_deck
    except Exception as e:
        raise HTTPException(500, f"deep_deck_import_failed: {e}")

    async def stream():
        try:
            async for evt in orchestrate_deep_deck(
                project_slug=project_slug,
                user_id=user.get("id"),
                session_id=session_id,
                agent_name=agent_name,
                messages=messages,
                config=config,
                auth_token=auth_token,
            ):
                yield f"data: {safe_dumps(evt)}\n\n"
        except Exception as e:
            err = {"stage": "error", "status": "failed", "message": str(e)[:400]}
            yield f"data: {safe_dumps(err)}\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.post("/deep-runs/{run_id}/approve")
async def approve_deep_run(run_id: int, request: Request) -> Dict[str, Any]:
    """User clicked APPROVE on the outline preview. Unblocks orchestrator's
    asyncio.Event so stages 4-7 can run.

    Body (optional):
        kept_gap_indices: [int, ...]  - if user edited gap list, indices of
            gaps/queries to keep. Empty/missing = keep all.
    """
    user = _get_user(request)
    if not user:
        raise HTTPException(401, "auth_required")
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    try:
        from dash.tools.deep_deck import signal_approval
    except Exception as e:
        raise HTTPException(500, f"deep_deck_import_failed: {e}")
    ok = signal_approval(run_id, payload or None)
    if not ok:
        # Run not waiting (already past gate, or expired)
        raise HTTPException(404, "run_not_waiting_for_approval")
    return {"ok": True, "run_id": run_id}


@router.get("/deep-runs")
def list_deep_runs(request: Request, project_slug: str = "", limit: int = 50) -> Dict[str, Any]:
    """List past deep-deck runs for a project (most recent first)."""
    _get_user(request)
    eng = _engine()
    if eng is None:
        return {"runs": []}
    try:
        from sqlalchemy import text
        with eng.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT id, project_slug, status, current_stage, stage_progress,
                           pres_id, cost_usd, started_at, finished_at, error_text
                    FROM dash.dash_deep_deck_runs
                    WHERE project_slug = :s OR :s = ''
                    ORDER BY started_at DESC
                    LIMIT :n
                    """
                ),
                {"s": project_slug, "n": limit},
            ).mappings().all()
        return {"runs": [dict(r) for r in rows]}
    except Exception as e:
        logger.warning("list_deep_runs failed: %s", e)
        return {"runs": [], "error": str(e)}


@router.get("/deep-runs/{run_id}")
def get_deep_run(run_id: int, request: Request) -> Dict[str, Any]:
    """Full detail of one run incl. gaps + executed queries (for audit / replay)."""
    _get_user(request)
    eng = _engine()
    if eng is None:
        raise HTTPException(500, "no_engine")
    try:
        from sqlalchemy import text
        with eng.connect() as conn:
            run = conn.execute(
                text("SELECT * FROM dash.dash_deep_deck_runs WHERE id = :id"),
                {"id": run_id},
            ).mappings().first()
            if not run:
                raise HTTPException(404, "run_not_found")
            gaps = conn.execute(
                text(
                    "SELECT id, rank, question, rationale, priority, status "
                    "FROM dash.dash_deep_deck_gaps WHERE run_id = :id ORDER BY rank"
                ),
                {"id": run_id},
            ).mappings().all()
            queries = conn.execute(
                text(
                    "SELECT id, gap_id, rank, question, sql_text, status, "
                    "row_count, columns, rows_preview, error_text, duration_ms "
                    "FROM dash.dash_deep_deck_queries WHERE run_id = :id ORDER BY rank"
                ),
                {"id": run_id},
            ).mappings().all()
        return {
            "run": dict(run),
            "gaps": [dict(g) for g in gaps],
            "queries": [dict(q) for q in queries],
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"db_error: {e}")
