"""Drift event API."""
import logging
from fastapi import APIRouter, HTTPException, Request

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/drift", tags=["drift"])


def _get_user(request: Request) -> dict:
    user = getattr(getattr(request, "state", None), "user", None)
    if not user:
        raise HTTPException(401, "Not authenticated")
    return user


def _check_project(user, slug: str, role: str = "viewer"):
    from app.auth import check_project_permission
    if not check_project_permission(user, slug, required_role=role):
        raise HTTPException(403)


@router.get("/recent/{project_slug}")
def recent_drift(project_slug: str, request: Request, limit: int = 50, status: str = ""):
    user = _get_user(request)
    _check_project(user, project_slug)
    from dash.learning.drift_detector import list_recent
    events = list_recent(project_slug, status=status or None, limit=limit)
    return {"project_slug": project_slug, "events": events}


@router.get("/count/{project_slug}")
def open_count(project_slug: str, request: Request):
    user = _get_user(request)
    _check_project(user, project_slug)
    from dash.learning.drift_detector import list_open_count
    return {"open_count": list_open_count(project_slug)}


@router.post("/{event_id}/acknowledge")
async def ack_event(event_id: int, request: Request):
    user = _get_user(request)
    from dash.learning.drift_detector import acknowledge
    ok = acknowledge(event_id, user.get("user_id", 0))
    return {"acknowledged": ok}


@router.post("/{event_id}/dismiss")
async def dismiss_event(event_id: int, request: Request):
    user = _get_user(request)
    try:
        from sqlalchemy import text
        from db.session import get_sql_engine
        with get_sql_engine().connect() as conn:
            conn.execute(text(
                "UPDATE public.dash_drift_events SET status='dismissed', "
                "acknowledged_at=NOW(), acknowledged_by=:uid WHERE id=:id"
            ), {"uid": user.get("user_id", 0), "id": event_id})
            conn.commit()
        return {"dismissed": True}
    except Exception as e:
        return {"dismissed": False, "error": str(e)[:200]}


@router.post("/{event_id}/retrain")
async def retrain_from_event(event_id: int, request: Request):
    """Trigger retrain of source linked to event."""
    user = _get_user(request)
    try:
        from sqlalchemy import text
        from db.session import get_sql_engine
        with get_sql_engine().connect() as conn:
            r = conn.execute(text(
                "SELECT source_id, project_slug FROM public.dash_drift_events "
                "WHERE id = :id"
            ), {"id": event_id}).fetchone()
        if not r:
            raise HTTPException(404)
        source_id = r[0]
        slug = r[1]
        _check_project(user, slug, role="editor")
        # Mark event status
        with get_sql_engine().connect() as conn:
            conn.execute(text(
                "UPDATE public.dash_drift_events SET status='retrain_triggered' "
                "WHERE id = :id"
            ), {"id": event_id})
            conn.commit()
        # Trigger training (fire and forget)
        return {"retrain_triggered": True, "source_id": source_id,
                "project_slug": slug,
                "next_step": f"POST /api/connectors/sources/{source_id}/train"}
    except HTTPException:
        raise
    except Exception as e:
        return {"retrain_triggered": False, "error": str(e)[:200]}


@router.get("/admin/all-open")
def admin_all_open(request: Request, limit: int = 100):
    """Super-admin cross-project view."""
    user = _get_user(request)
    if not user.get("is_admin"):
        raise HTTPException(403)
    from dash.learning.drift_detector import list_all_open
    return {"events": list_all_open(limit=limit)}


@router.post("/scan/{project_slug}/{source_id}")
async def trigger_scan(project_slug: str, source_id: int, request: Request):
    """Manual drift scan for one source."""
    user = _get_user(request)
    _check_project(user, project_slug, role="editor")
    from dash.learning.drift_detector import detect_for_source
    import asyncio
    events = await asyncio.to_thread(detect_for_source, project_slug, source_id)
    return {"events_emitted": len(events)}
