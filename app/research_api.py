"""
Deep Research REST API — Phase 3.

Endpoints:
  POST /api/research/deep                 kick off pipeline (sync default, ?async=true for bg)
  GET  /api/research/{run_id}             status + JSON result (no PDF blob)
  GET  /api/research/{run_id}/pdf         streams PDF
  GET  /api/research/runs?project_slug=…  list recent runs

Pipeline (dash.tools.deep_research.DeepResearch) may ship in parallel — import is
wrapped; missing pipeline returns 503 fail-soft, never crashes module import.

In-memory run store keyed by uuid4, evicts oldest at MAX_RUNS=50, no TTL daemon
(simple internal platform — operator restart clears history).
"""
from __future__ import annotations

import asyncio
import io
import logging
import time
import uuid
from collections import OrderedDict
from threading import Lock
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/research", tags=["research"])


# ── Auth helper (mirrors actions_api / accuracy_api) ──────────────────────

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


# ── In-memory run store (bounded LRU, thread-safe) ────────────────────────

MAX_RUNS = 50
SYNC_TIMEOUT_S = 90.0

_runs: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
_runs_lock = Lock()


def _store_run(run_id: str, payload: Dict[str, Any]) -> None:
    with _runs_lock:
        if run_id in _runs:
            _runs.move_to_end(run_id)
        _runs[run_id] = payload
        while len(_runs) > MAX_RUNS:
            _runs.popitem(last=False)


def _update_run(run_id: str, patch: Dict[str, Any]) -> None:
    with _runs_lock:
        if run_id in _runs:
            _runs[run_id].update(patch)
            _runs.move_to_end(run_id)


def _get_run(run_id: str) -> Optional[Dict[str, Any]]:
    with _runs_lock:
        return _runs.get(run_id)


def _list_runs(project_slug: Optional[str], limit: int) -> list[Dict[str, Any]]:
    with _runs_lock:
        items = list(_runs.values())
    items.sort(key=lambda r: r.get("created_at", 0), reverse=True)
    if project_slug:
        items = [r for r in items if r.get("project_slug") == project_slug]
    out = []
    for r in items[:limit]:
        out.append({
            "run_id": r.get("run_id"),
            "project_slug": r.get("project_slug"),
            "question": r.get("question"),
            "status": r.get("status"),
            "created_at": r.get("created_at"),
            "finished_at": r.get("finished_at"),
            "error": r.get("error"),
            "has_pdf": bool(r.get("pdf_bytes")),
        })
    return out


# ── Pipeline loader (fail-soft) ───────────────────────────────────────────

def _load_pipeline():
    """Import DeepResearch lazily. Returns class or None."""
    try:
        from dash.tools.deep_research import DeepResearch  # type: ignore
        return DeepResearch
    except Exception as exc:
        logger.warning("DeepResearch pipeline unavailable: %s", exc)
        return None


# ── Runner ────────────────────────────────────────────────────────────────

async def _run_pipeline(run_id: str, question: str, project_slug: str) -> None:
    DeepResearch = _load_pipeline()
    if DeepResearch is None:
        _update_run(run_id, {
            "status": "error",
            "error": "deep_research pipeline not available (503)",
            "finished_at": time.time(),
        })
        return
    try:
        dr = DeepResearch()
        result = await dr.run(question=question, project_slug=project_slug)
        if not isinstance(result, dict):
            result = {"raw": result}
        _update_run(run_id, {
            "status": "done",
            "spec": result.get("spec"),
            "hypothesis_tree": result.get("hypothesis_tree"),
            "findings": result.get("findings"),
            "recommendations": result.get("recommendations"),
            "pdf_bytes": result.get("pdf_bytes"),
            "stages_log": result.get("stages_log"),
            "finished_at": time.time(),
        })
    except Exception as exc:
        logger.exception("research run %s failed", run_id)
        _update_run(run_id, {
            "status": "error",
            "error": str(exc),
            "finished_at": time.time(),
        })


# ── Pydantic models ───────────────────────────────────────────────────────

class DeepRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=4000)
    project_slug: str = Field(..., min_length=1, max_length=200)


class RunSummary(BaseModel):
    run_id: str
    project_slug: str
    question: str
    status: str
    created_at: float
    finished_at: Optional[float] = None
    error: Optional[str] = None
    has_pdf: bool = False


# ── Result projection (strip pdf_bytes) ───────────────────────────────────

def _project_result(run: Dict[str, Any]) -> Dict[str, Any]:
    pdf_url: Optional[str] = None
    if run.get("status") == "done" and run.get("pdf_bytes"):
        pdf_url = f"/api/research/{run['run_id']}/pdf"
    return {
        "run_id": run.get("run_id"),
        "project_slug": run.get("project_slug"),
        "question": run.get("question"),
        "status": run.get("status"),
        "created_at": run.get("created_at"),
        "finished_at": run.get("finished_at"),
        "error": run.get("error"),
        "spec": run.get("spec"),
        "hypothesis_tree": run.get("hypothesis_tree"),
        "findings": run.get("findings"),
        "recommendations": run.get("recommendations"),
        "stages_log": run.get("stages_log"),
        "pdf_url": pdf_url,
    }


# ── Endpoints ─────────────────────────────────────────────────────────────

@router.post("/deep")
async def deep_research(
    payload: DeepRequest,
    request: Request,
    async_mode: bool = Query(False, alias="async"),
) -> Dict[str, Any]:
    """Kick off deep research pipeline.

    Default sync: waits up to 90s, returns full JSON (no PDF bytes).
    `?async=true`: returns {run_id} immediately, runs in background.
    """
    user = _get_user(request)
    if _load_pipeline() is None:
        raise HTTPException(503, "deep_research pipeline not available")

    run_id = uuid.uuid4().hex
    now = time.time()
    record: Dict[str, Any] = {
        "run_id": run_id,
        "question": payload.question,
        "project_slug": payload.project_slug,
        "status": "running",
        "created_at": now,
        "finished_at": None,
        "error": None,
        "spec": None,
        "hypothesis_tree": None,
        "findings": None,
        "recommendations": None,
        "pdf_bytes": None,
        "stages_log": None,
        "user": user.get("username") if isinstance(user, dict) else None,
    }
    _store_run(run_id, record)

    if async_mode:
        asyncio.create_task(_run_pipeline(run_id, payload.question, payload.project_slug))
        return {"run_id": run_id, "status": "running", "async": True}

    # Sync path: bound by 90s timeout
    try:
        await asyncio.wait_for(
            _run_pipeline(run_id, payload.question, payload.project_slug),
            timeout=SYNC_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        _update_run(run_id, {
            "status": "timeout",
            "error": f"sync run exceeded {SYNC_TIMEOUT_S}s; check GET /api/research/{run_id}",
            "finished_at": time.time(),
        })

    run = _get_run(run_id) or record
    return _project_result(run)


@router.get("/runs")
def list_runs(
    request: Request,
    project_slug: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
) -> Dict[str, Any]:
    """List recent runs from in-memory store (most recent first)."""
    _get_user(request)
    return {"runs": _list_runs(project_slug, limit), "total_in_memory": len(_runs)}


@router.get("/{run_id}")
def get_run(run_id: str, request: Request) -> Dict[str, Any]:
    """Status + JSON result (no PDF blob)."""
    _get_user(request)
    run = _get_run(run_id)
    if not run:
        raise HTTPException(404, f"run {run_id} not found")
    return _project_result(run)


@router.get("/{run_id}/pdf")
def get_run_pdf(run_id: str, request: Request):
    """Stream PDF for a completed run."""
    _get_user(request)
    run = _get_run(run_id)
    if not run:
        raise HTTPException(404, f"run {run_id} not found")
    if run.get("status") != "done":
        raise HTTPException(409, f"run not done (status={run.get('status')})")
    pdf_bytes = run.get("pdf_bytes")
    if not pdf_bytes:
        raise HTTPException(404, "no PDF available for this run")
    slug = run.get("project_slug", "unknown")
    filename = f"research_{slug}_{run_id}.pdf"
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
