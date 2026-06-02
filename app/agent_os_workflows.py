"""
Cross-Agent Workflows Hub
=========================

Aggregates workflows across ALL agents a user owns + has been shared with.
Mounted at /api/agent-os/workflows.

Tables touched:
  - dash.dash_autonomous_workflows  (extended in migration 092)
  - public.dash_workflow_run_history  (created in migration 092)
  - public.dash_projects             (slug → id, name)
  - public.dash_project_shares       (visibility for shared workflows)
  - public.dash_audit_log            (mutation audit)

All JSONB inserts use CAST(:x AS jsonb) per PgBouncer + named-param collision rule.
All workflow refs schema-qualified as `dash.dash_autonomous_workflows`.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
import time
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import create_engine as _sa_create_engine, text
from sqlalchemy.pool import NullPool

from db import db_url
from app.auth import log_action
from dash.utils import safe_dumps

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agent-os/workflows", tags=["AgentOS Workflows"])


# ── Helpers ─────────────────────────────────────────────────────────────


def _get_user(request: Request) -> dict:
    user = getattr(getattr(request, "state", None), "user", None)
    if not user:
        raise HTTPException(401, "Not authenticated")
    return user


def _engine():
    """Returns a NullPool engine. Caller must dispose() in finally."""
    return _sa_create_engine(db_url, poolclass=NullPool)


def _role_rank(role: str) -> int:
    return {"viewer": 1, "editor": 2, "admin": 3}.get((role or "").lower(), 0)


# ── croniter (optional) ─────────────────────────────────────────────────


try:
    from croniter import croniter as _croniter  # type: ignore

    _HAS_CRONITER = True
except Exception:
    _croniter = None  # type: ignore
    _HAS_CRONITER = False


_CRON_RE = re.compile(
    r"^\s*\S+\s+\S+\s+\S+\s+\S+\s+\S+\s*$"
)  # 5 whitespace-separated fields


def _validate_cron(expr: str) -> bool:
    if not expr or not isinstance(expr, str):
        return False
    if _HAS_CRONITER:
        try:
            return bool(_croniter.is_valid(expr))
        except Exception:
            return False
    return bool(_CRON_RE.match(expr))


def _next_run_at(cron_expr: Optional[str], last_run: Optional[datetime]) -> Optional[str]:
    """Compute next firing time. Returns ISO string or None if croniter unavailable / invalid."""
    if not cron_expr or not _HAS_CRONITER:
        return None
    try:
        base = last_run or datetime.now(timezone.utc)
        if base.tzinfo is None:
            base = base.replace(tzinfo=timezone.utc)
        it = _croniter(cron_expr, base)
        nxt = it.get_next(datetime)
        if isinstance(nxt, datetime):
            if nxt.tzinfo is None:
                nxt = nxt.replace(tzinfo=timezone.utc)
            return nxt.isoformat()
    except Exception:
        return None
    return None


# ── Visibility / permission helpers ─────────────────────────────────────


def _get_visible_workflows(
    user_id: int,
    *,
    status: Optional[str] = None,
    agent_slug: Optional[str] = None,
    search: Optional[str] = None,
    scope: str = "all",
) -> tuple[list[dict], dict[str, int]]:
    """Run the JOIN across dash_autonomous_workflows + dash_projects + dash_project_shares.

    Returns (workflows, stats). Caller doesn't need to dispose engine — handled here.
    """
    eng = _engine()
    try:
        sql = """
        WITH owned AS (
          SELECT w.id,
                 w.name,
                 w.description,
                 w.project_slug,
                 p.name AS project_name,
                 p.agent_name,
                 'owned'::text AS ownership,
                 'admin'::text AS share_role,
                 w.status,
                 w.schedule_cron,
                 w.schedule_action,
                 w.schedule_email,
                 w.schedule_webhook,
                 w.max_cost_usd,
                 w.daily_cap_usd,
                 w.last_run_at,
                 w.last_error,
                 w.last_output,
                 w.template_name,
                 w.action,
                 w.created_at
            FROM dash.dash_autonomous_workflows w
            JOIN public.dash_projects p ON p.slug = w.project_slug
           WHERE w.owner_user_id = :uid OR p.user_id = :uid
        ),
        shared AS (
          SELECT w.id,
                 w.name,
                 w.description,
                 w.project_slug,
                 p.name AS project_name,
                 p.agent_name,
                 'shared'::text AS ownership,
                 s.role AS share_role,
                 w.status,
                 w.schedule_cron,
                 w.schedule_action,
                 w.schedule_email,
                 w.schedule_webhook,
                 w.max_cost_usd,
                 w.daily_cap_usd,
                 w.last_run_at,
                 w.last_error,
                 w.last_output,
                 w.template_name,
                 w.action,
                 w.created_at
            FROM dash.dash_autonomous_workflows w
            JOIN public.dash_projects p ON p.slug = w.project_slug
            JOIN public.dash_project_shares s ON s.project_id = p.id
           WHERE s.shared_with_user_id = :uid
             AND p.user_id <> :uid
             AND COALESCE(w.owner_user_id, 0) <> :uid
        )
        SELECT * FROM owned
        UNION ALL
        SELECT * FROM shared
        ORDER BY created_at DESC
        """
        with eng.connect() as cn:
            rows = cn.execute(text(sql), {"uid": user_id}).mappings().all()
    finally:
        eng.dispose()

    # Python-side filter (cheap, small N per user)
    out: list[dict] = []
    status_norm = (status or "all").lower().strip()
    search_norm = (search or "").lower().strip()
    agent_norm = (agent_slug or "").strip()
    scope_norm = (scope or "all").lower().strip()

    stats = {"total": 0, "owned": 0, "shared": 0, "active": 0, "paused": 0, "failed": 0}

    for r in rows:
        if scope_norm == "owned" and r["ownership"] != "owned":
            continue
        if scope_norm == "shared" and r["ownership"] != "shared":
            continue
        if agent_norm and r.get("project_slug") != agent_norm:
            continue

        st = (r.get("status") or "").lower()
        last_err = r.get("last_error")
        is_failed = bool(last_err)
        if status_norm == "active" and st != "active":
            continue
        if status_norm == "paused" and st not in ("paused", "pending"):
            continue
        if status_norm == "failed" and not is_failed:
            continue

        if search_norm:
            hay = " ".join([
                (r.get("name") or ""),
                (r.get("description") or ""),
                (r.get("project_name") or ""),
                (r.get("agent_name") or ""),
            ]).lower()
            if search_norm not in hay:
                continue

        last_output = r.get("last_output")
        last_output_preview = None
        if last_output is not None:
            try:
                txt = json.dumps(last_output, default=str)
                last_output_preview = txt[:300]
            except Exception:
                last_output_preview = None

        last_run_at = r.get("last_run_at")
        next_run = _next_run_at(r.get("schedule_cron"), last_run_at)

        out.append({
            "id": r["id"],
            "name": r["name"],
            "description": r.get("description"),
            "project_slug": r["project_slug"],
            "project_name": r.get("project_name"),
            "agent_name": r.get("agent_name"),
            "ownership": r["ownership"],
            "share_role": r.get("share_role") or ("admin" if r["ownership"] == "owned" else "viewer"),
            "status": r.get("status"),
            "schedule_cron": r.get("schedule_cron"),
            "schedule_action": r.get("schedule_action"),
            "schedule_email": r.get("schedule_email"),
            "schedule_webhook": r.get("schedule_webhook"),
            "max_cost_usd": float(r["max_cost_usd"]) if r.get("max_cost_usd") is not None else None,
            "daily_cap_usd": float(r["daily_cap_usd"]) if r.get("daily_cap_usd") is not None else None,
            "last_run_at": last_run_at.isoformat() if last_run_at else None,
            "last_status": "fail" if is_failed else ("ok" if last_run_at else None),
            "last_error": last_err,
            "last_output_preview": last_output_preview,
            "next_run_at": next_run,
            "template_name": r.get("template_name"),
            "action": r.get("action"),
        })

        stats["total"] += 1
        if r["ownership"] == "owned":
            stats["owned"] += 1
        else:
            stats["shared"] += 1
        if st == "active":
            stats["active"] += 1
        if st in ("paused", "pending"):
            stats["paused"] += 1
        if is_failed:
            stats["failed"] += 1

    if not _HAS_CRONITER:
        # Best-effort note — frontend can show "install croniter for next-run estimates"
        stats["_note"] = "croniter not installed; next_run_at unavailable"  # type: ignore

    return out, stats


def _check_workflow_permission(
    wf_id: int, user_id: int, required_role: str = "viewer"
) -> dict:
    """Return {workflow_id, project_slug, ownership, role} or raise HTTPException(403/404)."""
    eng = _engine()
    try:
        with eng.connect() as cn:
            row = cn.execute(text(
                """
                SELECT w.id, w.project_slug, w.owner_user_id, p.user_id AS proj_owner_id, p.id AS proj_id
                  FROM dash.dash_autonomous_workflows w
                  JOIN public.dash_projects p ON p.slug = w.project_slug
                 WHERE w.id = :id
                """
            ), {"id": wf_id}).mappings().first()
            if not row:
                raise HTTPException(404, "workflow not found")
            slug = row["project_slug"]
            # Ownership rule: explicit owner OR project owner.
            ownership = None
            if row.get("owner_user_id") == user_id or row.get("proj_owner_id") == user_id:
                ownership = "owned"
                effective_role = "admin"
            else:
                share = cn.execute(text(
                    """
                    SELECT role FROM public.dash_project_shares
                     WHERE project_id = :pid AND shared_with_user_id = :uid
                    """
                ), {"pid": row["proj_id"], "uid": user_id}).first()
                if not share:
                    raise HTTPException(403, "no access to this workflow")
                ownership = "shared"
                effective_role = share[0] or "viewer"
    finally:
        eng.dispose()

    if _role_rank(effective_role) < _role_rank(required_role):
        raise HTTPException(403, f"requires {required_role} role; you have {effective_role}")
    return {
        "workflow_id": wf_id,
        "project_slug": slug,
        "ownership": ownership,
        "role": effective_role,
    }


# ── Run history persistence (used by run-now AND runner.py) ─────────────


def _persist_run_start(wf_id: int, project_slug: str, trigger: str = "cron") -> str:
    """Insert a 'running' row. Returns run_id."""
    run_id = f"wfr_{secrets.token_hex(8)}"
    eng = _engine()
    try:
        with eng.begin() as cn:
            cn.execute(text(
                """
                INSERT INTO public.dash_workflow_run_history
                  (run_id, workflow_id, project_slug, started_at, status, triggered_by)
                VALUES
                  (:rid, :wid, :slug, NOW(), 'running', :trig)
                """
            ), {"rid": run_id, "wid": wf_id, "slug": project_slug, "trig": trigger})
    except Exception:
        logger.exception("persist_run_start failed wf=%s", wf_id)
    finally:
        eng.dispose()
    return run_id


def _persist_run_finish(
    run_id: str,
    status: str,
    output: Any = None,
    error: Optional[str] = None,
    cost_usd: float = 0.0,
    steps_completed: int = 0,
    steps_total: int = 0,
) -> None:
    """Update a run row to final state + cache last_output on workflow row."""
    eng = _engine()
    try:
        with eng.begin() as cn:
            row = cn.execute(text(
                "SELECT workflow_id, started_at FROM public.dash_workflow_run_history WHERE run_id = :rid"
            ), {"rid": run_id}).first()
            duration_ms = None
            workflow_id = None
            if row:
                workflow_id = row[0]
                started = row[1]
                if started:
                    try:
                        if started.tzinfo is None:
                            started = started.replace(tzinfo=timezone.utc)
                        duration_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
                    except Exception:
                        duration_ms = None

            output_json = None
            if output is not None:
                try:
                    output_json = json.dumps(output, default=str)
                except Exception:
                    output_json = json.dumps({"_warn": "non-serializable output"})

            cn.execute(text(
                """
                UPDATE public.dash_workflow_run_history
                   SET finished_at = NOW(),
                       duration_ms = :dur,
                       status = :st,
                       output = CASE WHEN :out IS NULL THEN output ELSE CAST(:out AS jsonb) END,
                       error = :err,
                       cost_usd = :cost,
                       steps_completed = :sc,
                       steps_total = :stt
                 WHERE run_id = :rid
                """
            ), {
                "rid": run_id, "dur": duration_ms, "st": status,
                "out": output_json, "err": error, "cost": cost_usd,
                "sc": steps_completed, "stt": steps_total,
            })

            if workflow_id is not None and output_json is not None:
                cn.execute(text(
                    "UPDATE dash.dash_autonomous_workflows SET last_output = CAST(:out AS jsonb) WHERE id = :wid"
                ), {"out": output_json, "wid": workflow_id})
    except Exception:
        logger.exception("persist_run_finish failed run=%s", run_id)
    finally:
        eng.dispose()


# ── Endpoints ───────────────────────────────────────────────────────────


@router.get("")
def list_all_workflows(
    request: Request,
    status: Optional[str] = None,
    agent_slug: Optional[str] = None,
    search: Optional[str] = None,
    scope: str = "all",
):
    """List ALL workflows visible to current user (owned + shared)."""
    user = _get_user(request)
    workflows, stats = _get_visible_workflows(
        user["user_id"], status=status, agent_slug=agent_slug, search=search, scope=scope,
    )
    return {"workflows": workflows, "stats": stats}


@router.patch("/{wf_id}/cron")
async def update_cron(wf_id: int, request: Request):
    """Set or clear schedule_cron + related fields. Editor+ required."""
    user = _get_user(request)
    perm = _check_workflow_permission(wf_id, user["user_id"], required_role="editor")

    try:
        body = await request.json()
    except Exception:
        body = {}

    allowed = {
        "schedule_cron", "schedule_action", "schedule_email", "schedule_webhook",
        "max_cost_usd", "daily_cap_usd",
    }
    patch = {k: v for k, v in body.items() if k in allowed}
    if not patch:
        raise HTTPException(400, "no valid fields to update")

    if "schedule_cron" in patch and patch["schedule_cron"]:
        if not _validate_cron(patch["schedule_cron"]):
            raise HTTPException(400, f"invalid cron expression: {patch['schedule_cron']}")

    set_parts = []
    params: dict[str, Any] = {"id": wf_id}
    for k, v in patch.items():
        set_parts.append(f"{k} = :{k}")
        params[k] = v
    set_clause = ", ".join(set_parts)

    eng = _engine()
    try:
        with eng.begin() as cn:
            cn.execute(text(
                f"UPDATE dash.dash_autonomous_workflows SET {set_clause} WHERE id = :id"
            ), params)
    finally:
        eng.dispose()

    log_action(
        user, "workflow.cron.updated", "workflow", str(wf_id),
        json.dumps({"project_slug": perm["project_slug"], "patch": patch}, default=str),
    )
    return {"ok": True, "workflow_id": wf_id, "patch": patch}


def _enqueue_run(wf_id: int, project_slug: str, owner_user_id: int,
                 source: str = "manual") -> str:
    """Insert a queued run row for the workflow_runner daemon to pick up."""
    run_id = f"wfr_{secrets.token_hex(8)}"
    eng = _engine()
    try:
        with eng.begin() as cn:
            cn.execute(text(
                """
                INSERT INTO public.dash_workflow_run_history
                  (run_id, workflow_id, project_slug, started_at, enqueued_at,
                   status, triggered_by, source, owner_user_id)
                VALUES
                  (:rid, :wid, :slug, NOW(), NOW(),
                   'queued', :src, :src, :uid)
                """
            ), {"rid": run_id, "wid": wf_id, "slug": project_slug,
                "src": source, "uid": owner_user_id})
    finally:
        eng.dispose()
    return run_id


def _trigger_run(wf_id: int, request: Request, source: str = "manual") -> dict:
    """Shared handler for /run and /run-now — queues a run for the daemon."""
    user = _get_user(request)
    perm = _check_workflow_permission(wf_id, user["user_id"], required_role="editor")
    project_slug = perm["project_slug"]

    eng = _engine()
    try:
        with eng.connect() as cn:
            row = cn.execute(text(
                "SELECT id FROM dash.dash_autonomous_workflows WHERE id = :id"
            ), {"id": wf_id}).fetchone()
    finally:
        eng.dispose()
    if not row:
        raise HTTPException(404, "workflow not found")

    run_id = _enqueue_run(wf_id, project_slug, user["user_id"], source=source)
    log_action(
        user, "workflow.run.enqueued", "workflow", str(wf_id),
        json.dumps({"project_slug": project_slug, "run_id": run_id, "source": source}, default=str),
    )
    return {
        "run_id": run_id,
        "status": "queued",
        "source": source,
        "stream_url": f"/api/agent-os/workflows/runs/{run_id}/stream",
    }


@router.post("/{wf_id}/run")
def run_alias(wf_id: int, request: Request, source: Optional[str] = "manual"):
    """Alias of /run-now — enqueues a workflow run. Editor+ required.
    Accepts ?source= override (default 'manual')."""
    return _trigger_run(wf_id, request, source=source or "manual")


@router.post("/{wf_id}/run-now")
def run_now(wf_id: int, request: Request, source: Optional[str] = "manual"):
    """Enqueue a workflow run for the daemon. Editor+ required."""
    return _trigger_run(wf_id, request, source=source or "manual")


@router.get("/runs/{run_id}")
def get_run_detail(run_id: str, request: Request):
    """Return current state of a workflow run + parent workflow name.
    Used by the split-page UI to seed initial state before SSE connects
    (fixes fast runs where the page mounts after the run already finished)."""
    _ = _get_user(request)
    eng = _engine()
    try:
        with eng.begin() as cn:
            row = cn.execute(text(
                "SELECT h.run_id, h.workflow_id, h.project_slug, h.status, "
                "       h.steps_completed, h.steps_total, h.duration_ms, "
                "       h.dashboard_id, h.error, h.started_at, h.finished_at, "
                "       h.output, w.name AS workflow_name "
                "  FROM public.dash_workflow_run_history h "
                "  LEFT JOIN dash.dash_autonomous_workflows w ON w.id = h.workflow_id "
                " WHERE h.run_id = :rid"
            ), {"rid": run_id}).mappings().fetchone()
    finally:
        eng.dispose()
    if not row:
        raise HTTPException(404, "run not found")
    out = dict(row)
    # JSONB output column may contain {"events": [...]} or panel data — surface as-is
    if out.get("output") and isinstance(out["output"], dict):
        evs = out["output"].get("events")
        if isinstance(evs, list):
            out["events"] = evs
    # Stringify datetimes for JSON
    for k in ("started_at", "finished_at"):
        if out.get(k) is not None:
            out[k] = out[k].isoformat()
    return out


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: str, request: Request):
    """Best-effort cancellation. Sets status='cancelled' if still queued/running.
    Does NOT kill an already-executing tick — daemon will finish current step."""
    user = _get_user(request)
    eng = _engine()
    try:
        with eng.begin() as cn:
            row = cn.execute(text(
                "UPDATE public.dash_workflow_run_history "
                "   SET status='cancelled', finished_at=NOW() "
                " WHERE run_id=:rid AND status IN ('queued','running') "
                " RETURNING workflow_id"
            ), {"rid": run_id}).fetchone()
    finally:
        eng.dispose()
    if not row:
        raise HTTPException(404, "run not found or already finished")
    log_action(
        user, "workflow.run.cancelled", "workflow_run", run_id,
        json.dumps({"run_id": run_id}, default=str),
    )
    return {"ok": True, "run_id": run_id, "status": "cancelled"}


@router.get("/runs/{run_id}/stream")
async def stream_run(run_id: str, request: Request):
    """SSE stream: tails dash_traces rows for this run + emits run status.

    Yields:
      data: {event:'connected', run_id}
      data: {event:'trace', step_index, name, status, ms, rows, ...}
      ...
      data: {event:'done'|'failed'|'cancelled', dashboard_id?, error?}
    """
    from sqlalchemy import text as _t

    async def _gen():
        import time as _time
        yield "data: " + json.dumps({"event": "connected", "run_id": run_id}) + "\n\n"
        seen_ids: set[int] = set()
        last_heartbeat = _time.time()
        while True:
            if await request.is_disconnected():
                return
            eng = _engine()
            status = None
            dashboard_id = None
            error = None
            panel_count = 0
            new_rows: list[dict] = []
            try:
                with eng.connect() as cn:
                    rs = cn.execute(_t(
                        "SELECT status, dashboard_id, error FROM public.dash_workflow_run_history "
                        "WHERE run_id=:rid"
                    ), {"rid": run_id}).first()
                    if rs:
                        status, dashboard_id, error = rs[0], rs[1], rs[2]

                    # Tail traces written by the daemon, keyed by meta.run_id.
                    # Emit both step_done (workflow.step) and panel_ready (workflow.panel).
                    try:
                        rows = cn.execute(_t(
                            "SELECT id, name, status, duration_ms, started_at, meta "
                            "  FROM public.dash_traces "
                            " WHERE meta->>'run_id' = :rid "
                            " ORDER BY started_at ASC, id ASC"
                        ), {"rid": run_id}).mappings().all()
                        for r in rows:
                            rid_int = r["id"]
                            if rid_int in seen_ids:
                                continue
                            seen_ids.add(rid_int)
                            meta = r["meta"] or {}
                            name = r["name"] or ""
                            # Map workflow.panel → panel_ready event
                            if name == "workflow.panel":
                                new_rows.append({
                                    "event": "panel_ready",
                                    "step_index": meta.get("step_index"),
                                    "step_id": meta.get("step_id"),
                                    "panel_idx": meta.get("panel_idx"),
                                    "rows_count": meta.get("rows_count", 0),
                                })
                            else:
                                # Default: emit as trace (covers workflow.step → step_done)
                                ev = "step_done" if name == "workflow.step" else "trace"
                                new_rows.append({
                                    "event": ev,
                                    "name": name,
                                    "status": r["status"],
                                    "ms": r["duration_ms"],
                                    "meta": meta,
                                })
                    except Exception:
                        # dash_traces may not exist on minimal installs
                        pass

                    # Pull current panel count for final done event
                    if dashboard_id:
                        try:
                            pc = cn.execute(_t(
                                "SELECT COALESCE(jsonb_array_length(spec->'panels'), 0) "
                                "FROM public.dash_dashboards_v2 WHERE id=:did"
                            ), {"did": dashboard_id}).first()
                            if pc:
                                panel_count = int(pc[0])
                        except Exception:
                            pass
            except Exception as e:  # noqa: BLE001
                yield "data: " + json.dumps({"event": "error", "error": str(e)[:300]}) + "\n\n"
            finally:
                eng.dispose()

            for row in new_rows:
                yield "data: " + json.dumps(row, default=str) + "\n\n"

            if status == "done":
                yield "data: " + json.dumps({
                    "event": "done", "run_id": run_id,
                    "dashboard_id": dashboard_id, "panel_count": panel_count,
                }, default=str) + "\n\n"
                return
            if status in ("failed", "cancelled"):
                yield "data: " + json.dumps({
                    "event": "error" if status == "failed" else status,
                    "run_id": run_id,
                    "dashboard_id": dashboard_id,
                    "error": error,
                }, default=str) + "\n\n"
                return

            # Heartbeat every ~15s (SSE comment style, doesn't reach onmessage)
            now = _time.time()
            if now - last_heartbeat >= 15.0:
                yield ": heartbeat\n\n"
                last_heartbeat = now
            await asyncio.sleep(1.0)

    return StreamingResponse(_gen(), media_type="text/event-stream")


@router.post("/{wf_id}/pause")
def pause_workflow(wf_id: int, request: Request):
    user = _get_user(request)
    perm = _check_workflow_permission(wf_id, user["user_id"], required_role="editor")
    eng = _engine()
    try:
        with eng.begin() as cn:
            cn.execute(text(
                "UPDATE dash.dash_autonomous_workflows SET status = 'paused' WHERE id = :id"
            ), {"id": wf_id})
    finally:
        eng.dispose()
    log_action(
        user, "workflow.paused", "workflow", str(wf_id),
        json.dumps({"project_slug": perm["project_slug"]}),
    )
    return {"ok": True, "workflow_id": wf_id, "status": "paused"}


@router.post("/{wf_id}/resume")
def resume_workflow(wf_id: int, request: Request):
    user = _get_user(request)
    perm = _check_workflow_permission(wf_id, user["user_id"], required_role="editor")
    eng = _engine()
    try:
        with eng.begin() as cn:
            cn.execute(text(
                "UPDATE dash.dash_autonomous_workflows SET status = 'active' WHERE id = :id"
            ), {"id": wf_id})
    finally:
        eng.dispose()
    log_action(
        user, "workflow.resumed", "workflow", str(wf_id),
        json.dumps({"project_slug": perm["project_slug"]}),
    )
    return {"ok": True, "workflow_id": wf_id, "status": "active"}


@router.get("/{wf_id}/history")
def get_history(wf_id: int, request: Request, limit: int = 20):
    user = _get_user(request)
    _check_workflow_permission(wf_id, user["user_id"], required_role="viewer")
    limit = max(1, min(int(limit or 20), 100))
    eng = _engine()
    try:
        with eng.connect() as cn:
            rows = cn.execute(text(
                """
                SELECT run_id, started_at, finished_at, duration_ms, status,
                       steps_completed, steps_total, cost_usd, output, error, triggered_by
                  FROM public.dash_workflow_run_history
                 WHERE workflow_id = :wid
                 ORDER BY started_at DESC
                 LIMIT :lim
                """
            ), {"wid": wf_id, "lim": limit}).mappings().all()
    finally:
        eng.dispose()

    runs = []
    for r in rows:
        runs.append({
            "run_id": r["run_id"],
            "started_at": r["started_at"].isoformat() if r["started_at"] else None,
            "finished_at": r["finished_at"].isoformat() if r["finished_at"] else None,
            "duration_ms": r["duration_ms"],
            "status": r["status"],
            "steps_completed": r["steps_completed"],
            "steps_total": r["steps_total"],
            "cost_usd": float(r["cost_usd"]) if r.get("cost_usd") is not None else 0.0,
            "output": r["output"],
            "error": r["error"],
            "triggered_by": r["triggered_by"],
        })
    return {"runs": runs}


@router.get("/{wf_id}/history/{run_id}")
def get_history_run(wf_id: int, run_id: str, request: Request):
    user = _get_user(request)
    _check_workflow_permission(wf_id, user["user_id"], required_role="viewer")
    eng = _engine()
    try:
        with eng.connect() as cn:
            row = cn.execute(text(
                """
                SELECT run_id, workflow_id, project_slug, started_at, finished_at,
                       duration_ms, status, steps_completed, steps_total, cost_usd,
                       output, error, triggered_by
                  FROM public.dash_workflow_run_history
                 WHERE run_id = :rid AND workflow_id = :wid
                """
            ), {"rid": run_id, "wid": wf_id}).mappings().first()
    finally:
        eng.dispose()
    if not row:
        raise HTTPException(404, "run not found")
    return {
        "run_id": row["run_id"],
        "workflow_id": row["workflow_id"],
        "project_slug": row["project_slug"],
        "started_at": row["started_at"].isoformat() if row["started_at"] else None,
        "finished_at": row["finished_at"].isoformat() if row["finished_at"] else None,
        "duration_ms": row["duration_ms"],
        "status": row["status"],
        "steps_completed": row["steps_completed"],
        "steps_total": row["steps_total"],
        "cost_usd": float(row["cost_usd"]) if row.get("cost_usd") is not None else 0.0,
        "output": row["output"],
        "error": row["error"],
        "triggered_by": row["triggered_by"],
    }


# ── Live tail (SSE) ─────────────────────────────────────────────────────


_HEARTBEAT_S = 15.0


@router.get("/live-tail")
async def live_tail(request: Request):
    """SSE stream: replay last 50 events for visible workflows, then poll for new ones."""
    user = _get_user(request)
    uid = user["user_id"]

    async def event_gen():
        # Initial replay
        eng = _engine()
        try:
            with eng.connect() as cn:
                rows = cn.execute(text(
                    """
                    SELECT h.run_id, h.workflow_id, h.project_slug, h.started_at,
                           h.finished_at, h.status, h.error, h.triggered_by,
                           w.name AS workflow_name
                      FROM public.dash_workflow_run_history h
                      JOIN dash.dash_autonomous_workflows w ON w.id = h.workflow_id
                      JOIN public.dash_projects p ON p.slug = h.project_slug
                      LEFT JOIN public.dash_project_shares s
                             ON s.project_id = p.id AND s.shared_with_user_id = :uid
                     WHERE p.user_id = :uid
                        OR s.shared_with_user_id = :uid
                        OR w.owner_user_id = :uid
                     ORDER BY h.started_at DESC
                     LIMIT 50
                    """
                ), {"uid": uid}).mappings().all()
        finally:
            eng.dispose()

        last_seen_ts = None
        for r in reversed(rows):  # oldest first
            payload = {
                "run_id": r["run_id"], "workflow_id": r["workflow_id"],
                "workflow_name": r["workflow_name"], "project_slug": r["project_slug"],
                "status": r["status"], "error": r["error"],
                "triggered_by": r["triggered_by"],
                "started_at": r["started_at"].isoformat() if r["started_at"] else None,
                "finished_at": r["finished_at"].isoformat() if r["finished_at"] else None,
            }
            if r["started_at"] and (last_seen_ts is None or r["started_at"] > last_seen_ts):
                last_seen_ts = r["started_at"]
            yield f"data: {safe_dumps(payload)}\n\n"

        # Poll for new events
        while True:
            if await request.is_disconnected():
                break
            await asyncio.sleep(_HEARTBEAT_S)
            try:
                eng2 = _engine()
                try:
                    params: dict[str, Any] = {"uid": uid}
                    where_extra = ""
                    if last_seen_ts is not None:
                        where_extra = " AND h.started_at > :since"
                        params["since"] = last_seen_ts
                    with eng2.connect() as cn:
                        new_rows = cn.execute(text(
                            f"""
                            SELECT h.run_id, h.workflow_id, h.project_slug, h.started_at,
                                   h.finished_at, h.status, h.error, h.triggered_by,
                                   w.name AS workflow_name
                              FROM public.dash_workflow_run_history h
                              JOIN dash.dash_autonomous_workflows w ON w.id = h.workflow_id
                              JOIN public.dash_projects p ON p.slug = h.project_slug
                              LEFT JOIN public.dash_project_shares s
                                     ON s.project_id = p.id AND s.shared_with_user_id = :uid
                             WHERE (p.user_id = :uid OR s.shared_with_user_id = :uid OR w.owner_user_id = :uid)
                              {where_extra}
                             ORDER BY h.started_at ASC
                             LIMIT 50
                            """
                        ), params).mappings().all()
                finally:
                    eng2.dispose()
                if not new_rows:
                    yield "event: heartbeat\ndata: {}\n\n"
                    continue
                for r in new_rows:
                    if r["started_at"] and (last_seen_ts is None or r["started_at"] > last_seen_ts):
                        last_seen_ts = r["started_at"]
                    payload = {
                        "run_id": r["run_id"], "workflow_id": r["workflow_id"],
                        "workflow_name": r["workflow_name"], "project_slug": r["project_slug"],
                        "status": r["status"], "error": r["error"],
                        "triggered_by": r["triggered_by"],
                        "started_at": r["started_at"].isoformat() if r["started_at"] else None,
                        "finished_at": r["finished_at"].isoformat() if r["finished_at"] else None,
                    }
                    yield f"data: {safe_dumps(payload)}\n\n"
            except Exception as e:
                logger.warning("live-tail poll error: %s", e)
                yield f"event: error\ndata: {safe_dumps({'msg': str(e)[:200]})}\n\n"

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Create workflow from chat ───────────────────────────────────────────


@router.post("/from-chat")
async def create_from_chat(request: Request):
    """Create workflow from a chat-driven analysis. Editor+ required on target project."""
    user = _get_user(request)
    try:
        body = await request.json()
    except Exception:
        body = {}

    project_slug = (body.get("project_slug") or "").strip()
    name = (body.get("name") or "").strip()
    if not project_slug or not name:
        raise HTTPException(400, "project_slug and name required")

    description = (body.get("description") or "").strip()
    steps = body.get("steps") or []
    schedule_cron = body.get("schedule_cron") or None
    schedule_action = body.get("schedule_action") or body.get("action") or "post_insight"
    chat_msg_id = body.get("chat_msg_id")

    # Permission check on target project
    from app.auth import check_project_permission

    perm = check_project_permission(user, project_slug, required_role="editor")
    if not perm:
        raise HTTPException(403, "editor role required on target project")

    if schedule_cron and not _validate_cron(schedule_cron):
        raise HTTPException(400, f"invalid cron expression: {schedule_cron}")

    # First step's SQL (if any) becomes resolved_query; full steps blob stored in last_output for now.
    resolved_query = ""
    for s in steps:
        if isinstance(s, dict) and s.get("sql"):
            resolved_query = s["sql"]
            break

    steps_blob = json.dumps({
        "steps": steps,
        "chat_msg_id": chat_msg_id,
        "created_from": "chat",
    }, default=str)

    eng = _engine()
    try:
        with eng.begin() as cn:
            row = cn.execute(text(
                """
                INSERT INTO dash.dash_autonomous_workflows
                  (project_slug, template_name, name, description, schedule,
                   resolved_query, action, status, owner_user_id, schedule_cron,
                   schedule_action, last_output)
                VALUES
                  (:slug, :tmpl, :name, :desc, :sched,
                   :rsql, :act, 'active', :uid, :cron,
                   :sact, CAST(:blob AS jsonb))
                RETURNING id
                """
            ), {
                "slug": project_slug,
                "tmpl": "chat",
                "name": name,
                "desc": description,
                "sched": "manual",
                "rsql": resolved_query,
                "act": schedule_action,
                "uid": user["user_id"],
                "cron": schedule_cron,
                "sact": schedule_action,
                "blob": steps_blob,
            }).first()
            wf_id = row[0]
    finally:
        eng.dispose()

    log_action(
        user, "workflow.created_from_chat", "workflow", str(wf_id),
        json.dumps({"project_slug": project_slug, "name": name, "chat_msg_id": chat_msg_id}, default=str),
    )
    return {"wf_id": wf_id, "project_slug": project_slug}
