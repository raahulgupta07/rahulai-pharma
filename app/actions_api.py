"""Action Registry + Pending Approval Queue API.

Phase 2 — REST endpoints for the agent-action registry and the
``dash_hitl_requests`` queue (filtered to ``operation='action_exec'``).

Endpoints (prefix ``/api/actions``):

* ``GET    /registry?project_id=N``           list actions
* ``POST   /registry``                        create action
* ``PATCH  /registry/{id}``                   update fields
* ``DELETE /registry/{id}?hard=true``         soft (or hard) delete
* ``GET    /pending?project_id=N``            list pending exec requests
* ``POST   /pending/{request_id}/approve``    approve request
* ``POST   /pending/{request_id}/reject``     reject request
* ``GET    /audit?project_id=N&days=30``      audit log

Tables touched:
* ``public.dash_action_registry`` (created by parallel agent — fail-soft 503)
* ``public.dash_hitl_requests``    (see dash/hitl/manager.py for shape)

Auth: matches metrics_api.py / approval_api.py — Bearer token via
``request.state.user`` populated by ``AuthMiddleware``.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/actions", tags=["actions"])


# ── Auth helpers (mirrors metrics_api.py / approval_api.py) ───────────────

def _get_user(request: Request) -> Dict[str, Any]:
    user = getattr(getattr(request, "state", None), "user", None)
    if not user:
        try:
            from app.auth import get_current_user  # type: ignore
            user = get_current_user(request)
        except Exception:
            user = None
    if not user:
        raise HTTPException(401, "Not authenticated")
    return user


def _username(user: Dict[str, Any]) -> str:
    return (
        user.get("username")
        or user.get("name")
        or user.get("email")
        or "admin"
    )


def _is_super_admin(user: Dict[str, Any]) -> bool:
    try:
        from app.auth import SUPER_ADMIN  # type: ignore
        return user.get("username") == SUPER_ADMIN
    except Exception:
        return False


# ── Engine accessors ──────────────────────────────────────────────────────

def _read_engine():
    from db.session import get_sql_engine  # type: ignore
    return get_sql_engine()


def _write_engine():
    try:
        from db.session import get_write_engine  # type: ignore
        return get_write_engine()
    except Exception:
        # Fall back to default engine if write engine not exported.
        from db.session import get_sql_engine  # type: ignore
        return get_sql_engine()


def _registry_unavailable() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail=(
            "dash_action_registry table not available yet. "
            "Run pending migrations or wait for the parallel build agent "
            "to create the table."
        ),
    )


def _is_missing_table_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return (
        "does not exist" in msg
        or "undefinedtable" in msg
        or "no such table" in msg
    )


# ── Pydantic models ───────────────────────────────────────────────────────

class ActionCreateBody(BaseModel):
    project_id: int
    name: str
    description: Optional[str] = None
    method: str = "POST"
    url_template: str
    header_template: Optional[Dict[str, Any]] = None
    body_template: Optional[Dict[str, Any]] = None
    requires_approval: bool = True
    min_approvals: int = 1


class ActionUpdateBody(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    method: Optional[str] = None
    url_template: Optional[str] = None
    header_template: Optional[Dict[str, Any]] = None
    body_template: Optional[Dict[str, Any]] = None
    requires_approval: Optional[bool] = None
    min_approvals: Optional[int] = None
    enabled: Optional[bool] = None


class RejectBody(BaseModel):
    reason: Optional[str] = ""


# ── Helpers ────────────────────────────────────────────────────────────────

def _row_to_dict(row) -> Dict[str, Any]:
    d = dict(row._mapping) if hasattr(row, "_mapping") else dict(row)
    # JSONB fields may come back as str on some drivers; normalize.
    for k in ("header_template", "body_template", "details"):
        v = d.get(k)
        if isinstance(v, str):
            try:
                d[k] = json.loads(v)
            except Exception:
                pass
    # Datetimes → isoformat for JSON responses.
    for k, v in list(d.items()):
        if isinstance(v, datetime):
            d[k] = v.isoformat()
    return d


# ── Registry endpoints ────────────────────────────────────────────────────

@router.get("/registry")
def list_registry(
    request: Request,
    project_id: int = Query(..., description="Project id to filter by"),
    include_disabled: bool = Query(False, description="Include disabled actions"),
) -> Dict[str, Any]:
    _get_user(request)
    sql = """
        SELECT id, project_id, name, description, method, url_template,
               header_template, body_template, requires_approval,
               min_approvals, enabled, created_by, created_at, updated_at
          FROM public.dash_action_registry
         WHERE project_id = :pid
    """
    if not include_disabled:
        sql += " AND enabled = TRUE"
    sql += " ORDER BY created_at DESC NULLS LAST, id DESC"
    try:
        with _read_engine().connect() as conn:
            rows = conn.execute(text(sql), {"pid": project_id}).fetchall()
    except ProgrammingError as exc:
        if _is_missing_table_error(exc):
            raise _registry_unavailable()
        raise
    except Exception as exc:
        if _is_missing_table_error(exc):
            raise _registry_unavailable()
        logger.warning("list_registry failed: %s", exc)
        raise HTTPException(500, "Failed to list registry") from exc
    return {"actions": [_row_to_dict(r) for r in rows], "count": len(rows)}


@router.post("/registry")
def create_action(body: ActionCreateBody, request: Request) -> Dict[str, Any]:
    user = _get_user(request)
    created_by = _username(user)

    method = (body.method or "POST").upper()
    if method not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
        raise HTTPException(400, f"unsupported method: {method}")

    params = {
        "pid": body.project_id,
        "name": body.name,
        "desc": body.description,
        "method": method,
        "url": body.url_template,
        "headers": json.dumps(body.header_template or {}),
        "body": json.dumps(body.body_template or {}),
        "req_approval": bool(body.requires_approval),
        "min_approvals": max(1, int(body.min_approvals or 1)),
        "creator": created_by,
    }
    sql = """
        INSERT INTO public.dash_action_registry
          (project_id, name, description, method, url_template,
           header_template, body_template, requires_approval,
           min_approvals, enabled, created_by, created_at, updated_at)
        VALUES
          (:pid, :name, :desc, :method, :url,
           CAST(:headers AS jsonb), CAST(:body AS jsonb),
           :req_approval, :min_approvals, TRUE, :creator, now(), now())
        RETURNING id, project_id, name, description, method, url_template,
                  header_template, body_template, requires_approval,
                  min_approvals, enabled, created_by, created_at, updated_at
    """
    try:
        with _write_engine().begin() as conn:
            row = conn.execute(text(sql), params).first()
    except ProgrammingError as exc:
        if _is_missing_table_error(exc):
            raise _registry_unavailable()
        raise
    except Exception as exc:
        if _is_missing_table_error(exc):
            raise _registry_unavailable()
        logger.warning("create_action failed: %s", exc)
        raise HTTPException(500, "Failed to create action") from exc
    return {"action": _row_to_dict(row)}


@router.patch("/registry/{action_id}")
def update_action(
    action_id: int, body: ActionUpdateBody, request: Request
) -> Dict[str, Any]:
    _get_user(request)
    fields = body.dict(exclude_unset=True)
    if not fields:
        raise HTTPException(400, "no fields to update")

    # Build dynamic SET clause; JSONB cols use CAST(:x AS jsonb).
    set_parts: List[str] = []
    params: Dict[str, Any] = {"id": action_id}

    JSONB_COLS = {"header_template", "body_template"}
    for k, v in fields.items():
        if k in JSONB_COLS:
            set_parts.append(f"{k} = CAST(:{k} AS jsonb)")
            params[k] = json.dumps(v or {})
        else:
            set_parts.append(f"{k} = :{k}")
            params[k] = v

    set_parts.append("updated_at = now()")
    sql = (
        "UPDATE public.dash_action_registry SET "
        + ", ".join(set_parts)
        + " WHERE id = :id RETURNING id, project_id, name, description, method, "
        "url_template, header_template, body_template, requires_approval, "
        "min_approvals, enabled, created_by, created_at, updated_at"
    )
    try:
        with _write_engine().begin() as conn:
            row = conn.execute(text(sql), params).first()
    except ProgrammingError as exc:
        if _is_missing_table_error(exc):
            raise _registry_unavailable()
        raise
    except Exception as exc:
        if _is_missing_table_error(exc):
            raise _registry_unavailable()
        logger.warning("update_action failed: %s", exc)
        raise HTTPException(500, "Failed to update action") from exc
    if not row:
        raise HTTPException(404, "action not found")
    return {"action": _row_to_dict(row)}


@router.delete("/registry/{action_id}")
def delete_action(
    action_id: int,
    request: Request,
    hard: bool = Query(False, description="Permanently delete the row"),
) -> Dict[str, Any]:
    _get_user(request)
    try:
        with _write_engine().begin() as conn:
            if hard:
                row = conn.execute(
                    text(
                        "DELETE FROM public.dash_action_registry "
                        "WHERE id = :id RETURNING id"
                    ),
                    {"id": action_id},
                ).first()
                if not row:
                    raise HTTPException(404, "action not found")
                return {"ok": True, "id": action_id, "deleted": "hard"}
            row = conn.execute(
                text(
                    "UPDATE public.dash_action_registry "
                    "SET enabled = FALSE, updated_at = now() "
                    "WHERE id = :id RETURNING id, enabled"
                ),
                {"id": action_id},
            ).first()
    except ProgrammingError as exc:
        if _is_missing_table_error(exc):
            raise _registry_unavailable()
        raise
    except HTTPException:
        raise
    except Exception as exc:
        if _is_missing_table_error(exc):
            raise _registry_unavailable()
        logger.warning("delete_action failed: %s", exc)
        raise HTTPException(500, "Failed to delete action") from exc
    if not row:
        raise HTTPException(404, "action not found")
    return {"ok": True, "id": action_id, "deleted": "soft"}


# ── Pending queue endpoints ───────────────────────────────────────────────

@router.get("/pending")
def list_pending(
    request: Request,
    project_id: Optional[int] = Query(None),
    project_slug: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
) -> Dict[str, Any]:
    """List pending action-exec HITL requests, optionally enriched with
    the matching dash_action_registry name + url preview.

    ``dash_hitl_requests`` uses ``state='pending'`` (see dash/hitl/manager.py)
    and ``operation`` instead of ``request_type``. We match
    ``operation='action_exec'``.
    """
    _get_user(request)

    where_parts = ["h.state = 'pending'", "h.operation = 'action_exec'"]
    params: Dict[str, Any] = {"lim": limit}
    if project_slug:
        where_parts.append("h.project_slug = :pslug")
        params["pslug"] = project_slug

    # Best-effort enrichment join: action_id lives in details JSONB, e.g.
    # details->>'action_id'. The registry table may not exist yet — wrap the
    # join in a LATERAL try by attempting the join first and falling back to
    # a plain query on failure.
    join_sql = f"""
        SELECT h.id, h.request_id, h.project_slug, h.agent_name, h.operation,
               h.details, h.state, h.requested_by, h.created_at, h.expires_at,
               r.id   AS action_id,
               r.name AS action_name,
               r.url_template AS action_url_template,
               r.project_id   AS action_project_id
          FROM public.dash_hitl_requests h
     LEFT JOIN public.dash_action_registry r
            ON r.id = NULLIF((h.details ->> 'action_id'), '')::int
         WHERE {" AND ".join(where_parts)}
    """
    if project_id is not None:
        join_sql += " AND r.project_id = :pid"
        params["pid"] = project_id
    join_sql += " ORDER BY h.created_at DESC LIMIT :lim"

    plain_sql = f"""
        SELECT h.id, h.request_id, h.project_slug, h.agent_name, h.operation,
               h.details, h.state, h.requested_by, h.created_at, h.expires_at,
               NULL::int  AS action_id,
               NULL::text AS action_name,
               NULL::text AS action_url_template,
               NULL::int  AS action_project_id
          FROM public.dash_hitl_requests h
         WHERE {" AND ".join(where_parts)}
         ORDER BY h.created_at DESC LIMIT :lim
    """

    try:
        with _read_engine().connect() as conn:
            try:
                rows = conn.execute(text(join_sql), params).fetchall()
            except Exception as exc:
                if _is_missing_table_error(exc):
                    # Registry table absent — return un-enriched rows.
                    params.pop("pid", None)
                    rows = conn.execute(text(plain_sql), params).fetchall()
                else:
                    raise
    except Exception as exc:
        logger.warning("list_pending failed: %s", exc)
        raise HTTPException(500, "Failed to list pending requests") from exc

    out: List[Dict[str, Any]] = []
    for r in rows:
        d = _row_to_dict(r)
        # Build a small URL preview for UI.
        d["url_preview"] = (d.get("action_url_template") or "")[:140]
        out.append(d)
    return {"requests": out, "count": len(out)}


@router.post("/pending/{request_id}/approve")
def approve_pending(request_id: str, request: Request) -> Dict[str, Any]:
    """Approve a pending HITL request.

    The shipped ``dash_hitl_requests`` schema (see ``dash/hitl/manager.py``)
    does NOT have ``approval_count`` or per-approver columns — it tracks a
    single ``state`` (pending → approved/rejected/expired) plus
    ``responded_by`` / ``response_at``. We honour ``min_approvals`` from the
    linked registry row when present by counting prior ``approval`` events
    appended into ``details->'approval_events'`` (JSONB) — but otherwise a
    single approval transitions ``state`` to ``approved``.
    """
    user = _get_user(request)
    responder = _username(user)

    eng = _write_engine()
    with eng.begin() as conn:
        row = conn.execute(
            text(
                """
                SELECT request_id, state, details
                  FROM public.dash_hitl_requests
                 WHERE request_id = :rid
                """
            ),
            {"rid": request_id},
        ).first()
        if not row:
            raise HTTPException(404, "request not found")
        if row.state != "pending":
            raise HTTPException(409, f"request not pending (state={row.state})")

        details = row.details or {}
        if isinstance(details, str):
            try:
                details = json.loads(details)
            except Exception:
                details = {}

        # Resolve required approvals from the linked registry row, if any.
        action_id = None
        try:
            action_id = int(details.get("action_id")) if details.get("action_id") else None
        except (TypeError, ValueError):
            action_id = None

        min_approvals = 1
        if action_id is not None:
            try:
                ar = conn.execute(
                    text(
                        "SELECT min_approvals FROM public.dash_action_registry "
                        "WHERE id = :id"
                    ),
                    {"id": action_id},
                ).first()
                if ar and ar.min_approvals:
                    min_approvals = max(1, int(ar.min_approvals))
            except Exception as exc:
                if not _is_missing_table_error(exc):
                    logger.warning("registry lookup failed: %s", exc)

        # Append this approval event.
        events = list(details.get("approval_events") or [])
        # Prevent the same user double-approving.
        for ev in events:
            if ev.get("by") == responder and ev.get("decision") == "approve":
                raise HTTPException(409, "you have already approved this request")
        events.append(
            {
                "by": responder,
                "decision": "approve",
                "at": datetime.now(timezone.utc).isoformat(),
            }
        )
        approvals = sum(1 for ev in events if ev.get("decision") == "approve")
        details["approval_events"] = events
        details["approval_count"] = approvals
        details["min_approvals"] = min_approvals

        if approvals >= min_approvals:
            # Transition to approved.
            conn.execute(
                text(
                    """
                    UPDATE public.dash_hitl_requests
                       SET state = 'approved',
                           responded_by = :by,
                           response_at = now(),
                           details = CAST(:d AS jsonb)
                     WHERE request_id = :rid
                       AND state = 'pending'
                    """
                ),
                {
                    "by": responder,
                    "d": json.dumps(details),
                    "rid": request_id,
                },
            )
            new_state = "approved"
        else:
            # Stay pending, just record the partial approval.
            conn.execute(
                text(
                    """
                    UPDATE public.dash_hitl_requests
                       SET details = CAST(:d AS jsonb)
                     WHERE request_id = :rid
                       AND state = 'pending'
                    """
                ),
                {"d": json.dumps(details), "rid": request_id},
            )
            new_state = "pending"

    return {
        "ok": True,
        "request_id": request_id,
        "state": new_state,
        "approvals": approvals,
        "min_approvals": min_approvals,
    }


@router.post("/pending/{request_id}/reject")
def reject_pending(
    request_id: str, body: RejectBody, request: Request
) -> Dict[str, Any]:
    user = _get_user(request)
    responder = _username(user)
    reason = (body.reason or "").strip()

    eng = _write_engine()
    with eng.begin() as conn:
        row = conn.execute(
            text(
                "SELECT state, details FROM public.dash_hitl_requests "
                "WHERE request_id = :rid"
            ),
            {"rid": request_id},
        ).first()
        if not row:
            raise HTTPException(404, "request not found")
        if row.state != "pending":
            raise HTTPException(409, f"request not pending (state={row.state})")

        details = row.details or {}
        if isinstance(details, str):
            try:
                details = json.loads(details)
            except Exception:
                details = {}
        details["rejected_by"] = responder
        details["rejected_at"] = datetime.now(timezone.utc).isoformat()
        if reason:
            details["reject_reason"] = reason

        conn.execute(
            text(
                """
                UPDATE public.dash_hitl_requests
                   SET state = 'rejected',
                       responded_by = :by,
                       response_at = now(),
                       details = CAST(:d AS jsonb)
                 WHERE request_id = :rid
                   AND state = 'pending'
                """
            ),
            {"by": responder, "d": json.dumps(details), "rid": request_id},
        )
    return {"ok": True, "request_id": request_id, "state": "rejected"}


# ── Audit ─────────────────────────────────────────────────────────────────

@router.get("/audit")
def audit(
    request: Request,
    project_id: Optional[int] = Query(None),
    project_slug: Optional[str] = Query(None),
    days: int = Query(30, ge=1, le=365),
    limit: int = Query(200, ge=1, le=1000),
) -> Dict[str, Any]:
    """List action-exec requests that were executed (approved) or rejected
    within the last ``days`` days.
    """
    _get_user(request)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    where = [
        "h.operation = 'action_exec'",
        "h.state IN ('approved','rejected','expired')",
        "(h.response_at >= :cutoff OR h.created_at >= :cutoff)",
    ]
    params: Dict[str, Any] = {"cutoff": cutoff, "lim": limit}
    if project_slug:
        where.append("h.project_slug = :pslug")
        params["pslug"] = project_slug

    join_sql = f"""
        SELECT h.id, h.request_id, h.project_slug, h.agent_name, h.operation,
               h.details, h.state, h.requested_by, h.responded_by,
               h.response_at, h.created_at, h.expires_at,
               r.id AS action_id, r.name AS action_name,
               r.project_id AS action_project_id
          FROM public.dash_hitl_requests h
     LEFT JOIN public.dash_action_registry r
            ON r.id = NULLIF((h.details ->> 'action_id'), '')::int
         WHERE {" AND ".join(where)}
    """
    if project_id is not None:
        join_sql += " AND r.project_id = :pid"
        params["pid"] = project_id
    join_sql += " ORDER BY COALESCE(h.response_at, h.created_at) DESC LIMIT :lim"

    plain_sql = f"""
        SELECT h.id, h.request_id, h.project_slug, h.agent_name, h.operation,
               h.details, h.state, h.requested_by, h.responded_by,
               h.response_at, h.created_at, h.expires_at,
               NULL::int AS action_id, NULL::text AS action_name,
               NULL::int AS action_project_id
          FROM public.dash_hitl_requests h
         WHERE {" AND ".join(where)}
         ORDER BY COALESCE(h.response_at, h.created_at) DESC LIMIT :lim
    """

    try:
        with _read_engine().connect() as conn:
            try:
                rows = conn.execute(text(join_sql), params).fetchall()
            except Exception as exc:
                if _is_missing_table_error(exc):
                    params.pop("pid", None)
                    rows = conn.execute(text(plain_sql), params).fetchall()
                else:
                    raise
    except Exception as exc:
        logger.warning("audit failed: %s", exc)
        raise HTTPException(500, "Failed to fetch audit") from exc

    return {
        "requests": [_row_to_dict(r) for r in rows],
        "count": len(rows),
        "days": days,
    }


__all__ = ["router"]
