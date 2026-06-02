"""HTTP endpoints for column classification.

Additive — kept separate from existing app/connectors.py so it can be
wired up (or removed) without touching the main connectors router.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import text

from dash.utils import safe_dumps

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/connectors", tags=["classifier"])


def _get_user(request: Request) -> dict:
    user = getattr(getattr(request, "state", None), "user", None)
    if not user:
        raise HTTPException(401, "Not authenticated")
    return user


def _check_editor(user: dict, project_slug: str) -> None:
    from app.auth import check_project_permission

    perm = check_project_permission(user, project_slug, required_role="editor")
    if not perm:
        raise HTTPException(403, "Editor access required")


def _lookup_project_slug(source_id: int) -> str:
    from db.session import get_sql_engine

    eng = get_sql_engine()
    with eng.connect() as conn:
        row = conn.execute(
            text(
                "SELECT project_slug FROM public.dash_data_sources "
                "WHERE id = :id"
            ),
            {"id": source_id},
        ).fetchone()
    if not row:
        raise HTTPException(404, "Source not found")
    return row[0]


def _resolve_provider(project_slug: str, source_id: int):
    """Find the in-memory provider that backs ``source_id`` for ``project_slug``."""
    from dash.providers import get_registry  # type: ignore

    reg = get_registry()
    # Best-effort: ensure project providers are loaded (registry is async).
    try:
        import asyncio

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Caller is already inside an event loop; skip blocking load.
                pass
            else:
                loop.run_until_complete(reg.load_for_project(project_slug))
        except RuntimeError:
            pass
    except Exception:
        pass

    for p in reg.list_for_project(project_slug):
        if getattr(p, "source_id", None) == source_id:
            return p
        try:
            if int(getattr(p, "id", -1)) == int(source_id):
                return p
        except (TypeError, ValueError):
            continue
    return None


@router.post("/sources/{source_id}/classify")
async def classify_source_endpoint(source_id: int, request: Request):
    """SSE stream that runs the classifier on already-trained artifacts."""
    user = _get_user(request)
    project_slug = _lookup_project_slug(source_id)
    _check_editor(user, project_slug)

    provider = _resolve_provider(project_slug, source_id)
    if provider is None:
        raise HTTPException(503, "Provider not loaded")

    from dash.providers.training_steps_v2 import classify_columns_step

    async def gen():
        try:
            async for ev in classify_columns_step(provider, source_id):
                payload = ev.__dict__ if hasattr(ev, "__dict__") else dict(ev)
                yield f"data: {safe_dumps(payload)}\n\n"
            yield "event: done\ndata: {}\n\n"
        except Exception as e:
            logger.exception("classify stream failed")
            yield f"event: error\ndata: {safe_dumps({'error': str(e)[:300]})}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.get("/sources/{source_id}/classification")
def get_classification(source_id: int, request: Request):
    """Read column_classification.json for a source."""
    _ = _get_user(request)  # auth required, but no role check needed for read
    project_slug = _lookup_project_slug(source_id)

    p = (
        Path("knowledge")
        / project_slug
        / f"source_{source_id}"
        / "column_classification.json"
    )
    if not p.exists():
        raise HTTPException(404, "No classification yet — run /classify first")
    try:
        return json.loads(p.read_text())
    except Exception as e:
        logger.exception("failed to read classification")
        raise HTTPException(500, f"Could not read classification: {e}")


# To activate, add to app/main.py:
#     from app.connectors_classify import router as classify_router
#     app.include_router(classify_router)
