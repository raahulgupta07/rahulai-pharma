"""Admin API for distilled insights (#1) — review gate over daemon proposals.

Endpoints (super-admin gated, mirrors app/cache_curator_api.py auth):
  GET  /api/projects/{slug}/insights          — list pending + approved insights
  POST /api/projects/{slug}/insights/run       — run the curator (dry_run default True)
  POST /api/insights/{id}/approve              — promote a pending insight into chat
  POST /api/insights/{id}/reject               — reject (kept out of chat)

A daemon-distilled insight lands as dash_insights(status='pending') + a
dash_company_brain(status='pending') row. Approve flips both to active so
get_brain_context starts injecting it; reject flips both out.

Own direct cp-db connection (autocommit) — same engine the curator writes with,
so reads/writes are consistent and bypass the pgbouncer read-only engine.
"""
from __future__ import annotations

import os
import logging

from fastapi import APIRouter, HTTPException, Query, Request

logger = logging.getLogger(__name__)

router = APIRouter(tags=["insights"])


def _conn():
    import psycopg
    c = psycopg.connect(
        host=os.getenv("GRAPH_DB_HOST", "dash-db"),
        port=int(os.getenv("GRAPH_DB_PORT", "5432")),
        user=os.getenv("DB_USER", "ai"),
        dbname=os.getenv("DB_DATABASE", "ai"),
        password=os.getenv("DB_PASS", ""),
        connect_timeout=8,
        autocommit=True,
    )
    return c, c.cursor()


def _get_user(request: Request) -> dict:
    user = getattr(getattr(request, "state", None), "user", None)
    if not user:
        raise HTTPException(401, "Not authenticated")
    return user


def _gate(request: Request):
    user = _get_user(request)
    if not user.get("is_super") and not user.get("is_super_admin"):
        raise HTTPException(403, "super-admin only")


@router.get("/api/projects/{slug}/insights")
def list_insights(slug: str, request: Request, status: str = Query("")):
    """List distilled insights for review. status='' → pending + approved."""
    _gate(request)
    try:
        c, cur = _conn()
        try:
            if status:
                cur.execute(
                    "SELECT id, kind, title, detail, evidence, status, brain_id, created_at "
                    "FROM public.dash_insights WHERE project_slug = %s AND status = %s "
                    "ORDER BY created_at DESC LIMIT 200", (slug, status))
            else:
                cur.execute(
                    "SELECT id, kind, title, detail, evidence, status, brain_id, created_at "
                    "FROM public.dash_insights WHERE project_slug = %s "
                    "AND status IN ('pending','approved') "
                    "ORDER BY (status='pending') DESC, created_at DESC LIMIT 200", (slug,))
            rows = cur.fetchall()
            items = [{
                "id": r[0], "kind": r[1], "title": r[2], "detail": r[3],
                "evidence": r[4], "status": r[5], "brain_id": r[6],
                "created_at": str(r[7]) if r[7] else None,
            } for r in rows]
            return {"ok": True, "count": len(items), "insights": items}
        finally:
            c.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("list_insights failed for %s: %s", slug, exc)
        return {"ok": False, "error": str(exc)[:300], "insights": []}


@router.post("/api/projects/{slug}/insights/run")
def run_insights(slug: str, request: Request, dry_run: bool = Query(True)):
    """Run the insight curator now. dry_run defaults True (preview, no write)."""
    _gate(request)
    try:
        from dash.learning.insight_curator import run_insight_curator
        return run_insight_curator(slug, dry_run=dry_run)
    except Exception as exc:  # noqa: BLE001
        logger.warning("run_insights failed for %s: %s", slug, exc)
        return {"ok": False, "error": str(exc)[:300], "insights": []}


def _set_status(insight_id: int, ins_status: str, brain_status: str) -> dict:
    c, cur = _conn()
    try:
        cur.execute(
            "UPDATE public.dash_insights SET status = %s WHERE id = %s RETURNING brain_id",
            (ins_status, int(insight_id)))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "insight not found")
        brain_id = row[0]
        if brain_id:
            cur.execute(
                "UPDATE public.dash_company_brain SET status = %s, updated_at = NOW() "
                "WHERE id = %s", (brain_status, int(brain_id)))
        return {"ok": True, "id": int(insight_id), "status": ins_status, "brain_id": brain_id}
    finally:
        c.close()


@router.post("/api/insights/{insight_id}/approve")
def approve_insight(insight_id: int, request: Request):
    """Promote a pending insight — its brain row goes active → injected into chat."""
    _gate(request)
    try:
        return _set_status(insight_id, "approved", "active")
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:300]}


@router.post("/api/insights/{insight_id}/reject")
def reject_insight(insight_id: int, request: Request):
    """Reject a pending insight — kept out of chat (brain row → rejected)."""
    _gate(request)
    try:
        return _set_status(insight_id, "rejected", "rejected")
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:300]}


# ── distilled memories (#5) — review gate over the self-distill daemon ────────

@router.get("/api/projects/{slug}/distilled")
def list_distilled(slug: str, request: Request):
    """List PENDING distilled memory facts awaiting admin approval."""
    _gate(request)
    try:
        c, cur = _conn()
        try:
            cur.execute(
                "SELECT id, fact, created_by, created_at FROM public.dash_memories "
                "WHERE project_slug = %s AND source = 'distilled' AND status = 'pending' "
                "ORDER BY created_at DESC LIMIT 200", (slug,))
            rows = cur.fetchall()
            items = [{"id": r[0], "fact": r[1], "from": r[2],
                      "created_at": str(r[3]) if r[3] else None} for r in rows]
            return {"ok": True, "count": len(items), "memories": items}
        finally:
            c.close()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:300], "memories": []}


def _set_memory_status(memory_id: int, status: str) -> dict:
    c, cur = _conn()
    try:
        cur.execute(
            "UPDATE public.dash_memories SET status = %s WHERE id = %s RETURNING id",
            (status, int(memory_id)))
        if not cur.fetchone():
            raise HTTPException(404, "memory not found")
        return {"ok": True, "id": int(memory_id), "status": status}
    finally:
        c.close()


@router.post("/api/memories/{memory_id}/approve")
def approve_memory(memory_id: int, request: Request):
    """Approve a distilled memory → active = injected into chat as a hint."""
    _gate(request)
    try:
        return _set_memory_status(memory_id, "active")
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:300]}


@router.post("/api/memories/{memory_id}/reject")
def reject_memory(memory_id: int, request: Request):
    """Reject a distilled memory — kept out of chat."""
    _gate(request)
    try:
        return _set_memory_status(memory_id, "rejected")
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:300]}
