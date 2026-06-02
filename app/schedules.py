"""
Schedules API
=============

CRUD for scheduled recurring queries per project.
"""

import json

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import create_engine as _sa_create_engine, text
from sqlalchemy.pool import NullPool

from db import db_url

router = APIRouter(prefix="/api/projects", tags=["Schedules"])
_engine = _sa_create_engine(db_url, poolclass=NullPool)

CRON_PRESETS = {
    "daily": "0 8 * * *",
    "weekly": "0 8 * * 1",
    "monthly": "0 8 1 * *",
}


def _get_user(request: Request) -> dict:
    user = getattr(getattr(request, 'state', None), 'user', None)
    if not user:
        raise HTTPException(401, "Not authenticated")
    return user


@router.get("/{slug}/schedules")
def list_schedules(slug: str, request: Request):
    """List all schedules for a project."""
    user = _get_user(request)
    with _engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, name, prompt, cron, timezone, enabled, output_type, email_to, last_run_at, created_at "
            "FROM public.dash_schedules WHERE project_slug = :slug AND user_id = :uid "
            "ORDER BY created_at DESC"
        ), {"slug": slug, "uid": user["user_id"]}).fetchall()

    schedules = [
        {"id": r[0], "name": r[1], "prompt": r[2], "cron": r[3], "timezone": r[4],
         "enabled": r[5], "output_type": r[6], "email_to": r[7],
         "last_run_at": str(r[8]) if r[8] else None, "created_at": str(r[9]) if r[9] else None}
        for r in rows
    ]
    return {"schedules": schedules}


@router.post("/{slug}/schedules")
def create_schedule(slug: str, request: Request, name: str = "Report", prompt: str = "",
                    cron: str = "weekly", output_type: str = "dashboard", email_to: str = ""):
    """Create a new schedule."""
    user = _get_user(request)
    if not prompt:
        raise HTTPException(400, "Prompt required")

    cron_expr = CRON_PRESETS.get(cron, cron)

    with _engine.connect() as conn:
        result = conn.execute(text(
            "INSERT INTO public.dash_schedules (project_slug, user_id, name, prompt, cron, output_type, email_to) "
            "VALUES (:slug, :uid, :name, :prompt, :cron, :otype, :email) RETURNING id"
        ), {"slug": slug, "uid": user["user_id"], "name": name, "prompt": prompt,
            "cron": cron_expr, "otype": output_type, "email": email_to or None})
        sched_id = result.fetchone()[0]
        conn.commit()

    return {"status": "ok", "id": sched_id}


@router.put("/{slug}/schedules/{schedule_id}")
def update_schedule(slug: str, schedule_id: int, request: Request,
                    name: str = "", prompt: str = "", cron: str = "", enabled: str = "",
                    output_type: str = "", email_to: str = ""):
    """Update a schedule."""
    user = _get_user(request)
    updates = []
    params: dict = {"id": schedule_id, "slug": slug, "uid": user["user_id"]}

    if name:
        updates.append("name = :name")
        params["name"] = name
    if prompt:
        updates.append("prompt = :prompt")
        params["prompt"] = prompt
    if cron:
        updates.append("cron = :cron")
        params["cron"] = CRON_PRESETS.get(cron, cron)
    if enabled:
        updates.append("enabled = :enabled")
        params["enabled"] = enabled.lower() == "true"
    if output_type:
        updates.append("output_type = :otype")
        params["otype"] = output_type
    if email_to is not None:
        updates.append("email_to = :email")
        params["email"] = email_to or None

    if updates:
        with _engine.connect() as conn:
            conn.execute(text(
                f"UPDATE public.dash_schedules SET {', '.join(updates)} "
                f"WHERE id = :id AND project_slug = :slug AND user_id = :uid"
            ), params)
            conn.commit()

    return {"status": "ok"}


@router.delete("/{slug}/schedules/{schedule_id}")
def delete_schedule(slug: str, schedule_id: int, request: Request):
    """Delete a schedule."""
    user = _get_user(request)
    with _engine.connect() as conn:
        conn.execute(text(
            "DELETE FROM public.dash_schedules WHERE id = :id AND project_slug = :slug AND user_id = :uid"
        ), {"id": schedule_id, "slug": slug, "uid": user["user_id"]})
        conn.commit()
    return {"status": "ok"}


@router.post("/{slug}/schedules/{schedule_id}/run")
async def run_schedule(slug: str, schedule_id: int, request: Request):
    """Manually trigger a scheduled report."""
    user = _get_user(request)

    with _engine.connect() as conn:
        row = conn.execute(text(
            "SELECT prompt, output_type FROM public.dash_schedules "
            "WHERE id = :id AND project_slug = :slug AND user_id = :uid"
        ), {"id": schedule_id, "slug": slug, "uid": user["user_id"]}).fetchone()

    if not row:
        raise HTTPException(404, "Schedule not found")

    prompt, output_type = row[0], row[1]

    # Run the query through the project team
    try:
        from dash.team import create_project_team
        from sqlalchemy import text as sa_text

        # Get project info
        with _engine.connect() as conn:
            proj = conn.execute(sa_text(
                "SELECT agent_name, agent_role, agent_personality FROM public.dash_projects WHERE slug = :s"
            ), {"s": slug}).fetchone()

        if not proj:
            raise HTTPException(404, "Project not found")

        team = create_project_team(
            project_slug=slug,
            agent_name=proj[0], agent_role=proj[1], agent_personality=proj[2],
        )
        response = team.run(prompt)
        content = response.content or ""

        # Save result
        with _engine.connect() as conn:
            conn.execute(sa_text(
                "UPDATE public.dash_schedules SET last_run_at = NOW(), last_result = CAST(:result AS jsonb) WHERE id = :id"
            ), {"result": json.dumps({"content": content}), "id": schedule_id})
            conn.commit()

        return {"status": "ok", "content": content}
    except Exception as e:
        return {"status": "error", "content": str(e)}
