"""Phase-0 dashboard spec persistence API."""
from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
import time
import traceback
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import text

from dash.dashboards.agent import DashboardAgent, DeepDashAgent
from dash.utils import safe_dumps
from dash.dashboards.chat_context import extract_context
from dash.dashboards.memory import get_preferences, log_action
from dash.dashboards.planner import generate_spec
from dash.dashboards.spec import DashboardSpec
from dash.tools.skill_refinery import _get_engine


def _sanitize_json(obj):
    """Walk dict/list, replace NaN/Inf floats with None (JSON-compliant).

    pandas/numpy floats in panel rows trip default FastAPI json.dumps. Run
    this on any DeepDashSpec / panel-data payload before return.
    """
    import math
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, dict):
        return {k: _sanitize_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_json(v) for v in obj]
    if isinstance(obj, tuple):
        return tuple(_sanitize_json(v) for v in obj)
    return obj


def _user_id(request: Request) -> str | None:
    user = getattr(getattr(request, "state", None), "user", None)
    if not user:
        return None
    return str(user.get("id") or user.get("user_id") or user.get("username") or "") or None

logger = logging.getLogger(__name__)

import os as _os_dbg


def _dbg_trace() -> str | None:
    """Return a full traceback ONLY when DEBUG is enabled — never leak internal
    file paths / code structure to API clients in production. Always logged."""
    if _os_dbg.getenv("DEBUG", "").strip().lower() in ("1", "true", "yes", "on"):
        return traceback.format_exc()
    return None


router = APIRouter(prefix="/api/dashboards", tags=["DashboardsV2"])


def _ensure_table():
    eng = _get_engine()
    with eng.begin() as conn:
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS public.dash_dashboards_v2 ("
            "id TEXT PRIMARY KEY, project_slug TEXT, spec JSONB, "
            "created_at TIMESTAMPTZ DEFAULT NOW())"
        ))


class GenerateRequest(BaseModel):
    project_slug: str
    prompt: str
    persona: str = ""
    chat_context: dict | None = None
    deepen: bool = False
    query_intent: str = "private"


def _attach_data(result: dict, project_slug: str) -> dict:
    if not result.get("ok") or not result.get("spec"):
        return result
    try:
        from dash.dashboards.runner import run_spec
        result["data"] = run_spec(result["spec"], project_slug)
    except Exception as e:
        logger.warning(f"attach data failed: {e}")
        result["data"] = {}
    return result


def _maybe_deepen(result: dict, project_slug: str, persona: str, deepen: bool) -> dict:
    if not deepen or not result.get("ok") or not result.get("spec"):
        return result
    try:
        agent = DashboardAgent(project_slug, result["spec"], persona=persona)
        out = agent.run_sync()
        result["spec"] = out["spec"]
        result["insights"] = out["insights"]
        result["rounds"] = out["rounds"]
        result["tokens_used"] = out["tokens_used"]
    except Exception as e:
        logger.warning(f"deepen failed: {e}")
        result["deepen_error"] = str(e)
    return result


@router.post("/generate")
def generate_dashboard(req: GenerateRequest, request: Request):
    try:
        res = generate_spec(req.project_slug, req.prompt, req.persona, req.chat_context,
                            user_id=_user_id(request))
        res = _maybe_deepen(res, req.project_slug, req.persona, req.deepen)
        return _attach_data(res, req.project_slug)
    except Exception as e:
        logger.exception("generate failed")
        return {"ok": False, "error": str(e), "trace": _dbg_trace()}


class FromChatRequest(BaseModel):
    thread_id: str
    msg_id: str | None = None
    project_slug: str
    prompt: str = ""
    deepen: bool = False


@router.post("/from-chat")
def generate_from_chat(req: FromChatRequest, request: Request):
    try:
        try:
            # If no msg_id provided, extract FULL thread (don't truncate)
            up_to = req.msg_id if (req.msg_id and str(req.msg_id).strip()) else None
            ctx = extract_context(req.thread_id, up_to)
        except Exception as e:
            logger.warning(f"extract_context failed: {e}")
            ctx = {"questions": [], "sqls": [], "results": [], "prior_results": [],
                   "insights": [], "filters_mentioned": [], "persona": ""}
        prompt = (req.prompt or "").strip()
        if not prompt:
            qs = ctx.get("questions") or []
            if len(qs) > 1:
                joined = " ".join(f"{i+1}) {q}" for i, q in enumerate(qs))
                prompt = f"Build dashboard covering all these questions: {joined}"[:2000]
            else:
                prompt = qs[-1] if qs else "Build a dashboard summarizing this conversation."
        persona = ctx.get("persona") or ""
        res = generate_spec(req.project_slug, prompt, persona, ctx, user_id=_user_id(request))
        res = _maybe_deepen(res, req.project_slug, persona, req.deepen)
        return _attach_data(res, req.project_slug)
    except Exception as e:
        logger.exception("from-chat failed")
        return {"ok": False, "error": str(e), "trace": _dbg_trace()}


class DeepenRequest(BaseModel):
    spec: dict
    project_slug: str
    persona: str = ""


@router.post("/deepen")
def deepen_dashboard(req: DeepenRequest):
    try:
        agent = DashboardAgent(req.project_slug, req.spec, persona=req.persona)
        out = agent.run_sync()
        result = {"ok": True, "spec": out["spec"], "insights": out["insights"],
                  "rounds": out["rounds"], "tokens_used": out["tokens_used"]}
        return _attach_data(result, req.project_slug)
    except Exception as e:
        logger.exception("deepen failed")
        return {"ok": False, "error": str(e), "trace": _dbg_trace(),
                "spec": req.spec, "insights": [], "rounds": 0, "tokens_used": 0}


@router.post("/deepen/stream")
async def deepen_dashboard_stream(req: DeepenRequest):
    agent = DashboardAgent(req.project_slug, req.spec, persona=req.persona)

    async def gen():
        try:
            async for event in agent.stream():
                yield f"data: {safe_dumps(event)}\n\n"
        except Exception as e:
            logger.warning(f"deepen stream failed: {e}")
            yield f"data: {safe_dumps({'type': 'error', 'error': str(e)})}\n\n"
        yield f"data: {safe_dumps({'type': 'done', 'spec': agent.spec, 'insights': agent.insights, 'tokens_used': agent.tokens_used})}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


class RunDataRequest(BaseModel):
    spec: dict
    project_slug: str


@router.post("/run-data")
async def run_data(req: RunDataRequest):
    from dash.dashboards.runner import run_spec
    try:
        return _sanitize_json({"ok": True, "data": run_spec(req.spec, req.project_slug)})
    except Exception as e:
        logger.exception("run-data failed")
        return {"ok": False, "error": str(e)}


class PatchRequest(BaseModel):
    spec: dict
    prompt: str


@router.post("/patch")
async def patch_dashboard(req: PatchRequest):
    from dash.dashboards.patcher import apply_patch, llm_patch
    result = llm_patch(req.spec, req.prompt)
    if "error" in result:
        return {"ok": False, "error": result["error"]}
    try:
        new_spec = apply_patch(req.spec, result["ops"])
    except Exception as e:
        return {"ok": False, "error": f"apply failed: {e}"}
    return {
        "ok": True,
        "spec": new_spec,
        "ops": result["ops"],
        "rationale": result.get("rationale", ""),
    }


class DeepBuildRequest(BaseModel):
    project_slug: str
    question: str
    persona: str = ""
    audience: str = "executive"
    n_panels: int = 8
    gen_model: str | None = None      # generator (e.g. google/gemini-3-flash)
    judge_model: str | None = None    # judge — MUST differ from gen_model


@router.post("/deep-build/stream")
async def deep_build_stream(req: DeepBuildRequest):
    """9-stage Deep Dash pipeline. SSE stream of stage events + final spec.

    Stages: intent → schema_rag → panel_plan → sql_gen → explain_gate →
    execute → chart_specs → judge (different-model) → layout.
    """
    if req.judge_model and req.gen_model and req.judge_model == req.gen_model:
        raise HTTPException(status_code=400, detail="judge_model must differ from gen_model (TACL self-bias)")

    agent = DeepDashAgent(
        project_slug=req.project_slug,
        question=req.question,
        persona=req.persona,
        audience=req.audience,
        n_panels=req.n_panels,
        gen_model=req.gen_model,
        judge_model=req.judge_model,
    )

    async def gen():
        try:
            async for event in agent.stream():
                yield f"data: {safe_dumps(event)}\n\n"
        except Exception as e:
            logger.exception("deep-build stream failed")
            yield f"data: {safe_dumps({'type': 'error', 'error': str(e)})}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


class FromChatStreamBody(BaseModel):
    project_slug: str
    session_id: str
    audience: str = "Exec"
    deepen: bool = True
    force_rebuild: bool = False


@router.post("/from-chat/stream")
async def from_chat_stream(req: FromChatStreamBody, request: Request):
    """SSE: build a Deep Dash dashboard from a whole chat session's context.

    Mirrors `/deep-build/stream` shape but seeds the agent's `question` from
    the synthesized prompt of the chat session (questions + SQLs already run),
    so the dashboard covers the full conversation rather than a single prompt.

    Fail-soft: any setup error (e.g. invalid session_id, missing project) is
    emitted as a single SSE `error` event then the stream closes.
    """
    async def _err_stream(detail: str):
        yield f"data: {safe_dumps({'type': 'error', 'detail': detail})}\n\n"

    # #15b — Compute signature: sha256(project|sorted_question_hashes|schema_fingerprint).
    # Fail-soft: any error → signature_hash=None and fall through to session cache.
    signature_hash: str | None = None
    try:
        try:
            _sig_ctx = extract_context(req.session_id, None)
        except Exception:
            _sig_ctx = {"questions": []}
        _qs_list = sorted([
            str((q.get("question") if isinstance(q, dict) else q) or "")
            for q in (_sig_ctx.get("questions") or [])
        ])[:5]
        qs_blob = "|".join(_qs_list)[:2000]
        sfp = ""
        try:
            eng_sig = _get_engine()
            with eng_sig.begin() as _c:
                sfp = _c.execute(text(
                    "SELECT COALESCE(MAX(updated_at)::text, '') FROM public.dash_table_metadata WHERE project_slug=:p"
                ), {"p": req.project_slug}).scalar() or ""
        except Exception:
            sfp = ""
        sig_input = f"{req.project_slug}|{qs_blob}|{sfp}"
        signature_hash = hashlib.sha256(sig_input.encode()).hexdigest()[:32]
    except Exception as _sige:
        logger.debug(f"signature compute failed (fail-soft): {_sige}")
        signature_hash = None

    # #15c — Signature-based cache check (precedes session-based).
    if not req.force_rebuild and signature_hash:
        try:
            from db.session import get_sql_engine as _get_ro_sig
            with _get_ro_sig().begin() as _conn:
                row_s = _conn.execute(text(
                    "SELECT id, spec, version FROM public.dash_dashboards_v2 "
                    "WHERE project_slug=:p AND signature_hash=:h "
                    "ORDER BY version DESC LIMIT 1"
                ), {"p": req.project_slug, "h": signature_hash}).fetchone()
            if row_s is not None:
                cached_spec = row_s[1]
                if isinstance(cached_spec, str):
                    try:
                        cached_spec = json.loads(cached_spec)
                    except Exception:
                        pass
                payload = {
                    "type": "done",
                    "spec": cached_spec,
                    "dashboard_id": row_s[0],
                    "version": int(row_s[2] or 1),
                    "cached": True,
                    "signature_match": True,
                }
                async def _cached_sig_stream():
                    yield f"data: {safe_dumps(payload)}\n\n"
                return StreamingResponse(_cached_sig_stream(), media_type="text/event-stream")
        except Exception as e:
            logger.warning(f"signature cache check failed: {e}")

    # Cache check — if not force_rebuild and a previous version exists for this
    # session, emit a single 'done' SSE with cached spec and exit (no pipeline).
    if not req.force_rebuild and req.session_id:
        try:
            from db.session import get_sql_engine as _get_ro
            with _get_ro().begin() as _conn:
                row = _conn.execute(text(
                    "SELECT id, spec, version FROM public.dash_dashboards_v2 "
                    "WHERE project_slug=:p AND session_id=:s "
                    "ORDER BY version DESC LIMIT 1"
                ), {"p": req.project_slug, "s": req.session_id}).fetchone()
            if row is not None:
                cached_spec = row[1]
                if isinstance(cached_spec, str):
                    try:
                        cached_spec = json.loads(cached_spec)
                    except Exception:
                        pass
                payload = {
                    "type": "done",
                    "spec": cached_spec,
                    "dashboard_id": row[0],
                    "version": int(row[2] or 1),
                    "cached": True,
                }

                async def _cached_stream():
                    yield f"data: {safe_dumps(payload)}\n\n"
                return StreamingResponse(_cached_stream(), media_type="text/event-stream")
        except Exception as e:
            logger.warning(f"from-chat/stream cache check failed: {e}")
            # fall through to fresh build

    # Setup — fail-soft, single error SSE event on any exception
    try:
        try:
            ctx = extract_context(req.session_id, None)
        except Exception as e:
            logger.warning(f"from-chat/stream extract_context failed: {e}")
            ctx = {"questions": [], "sqls": [], "prior_results": [],
                   "filters_mentioned": [], "persona": ""}

        questions = ctx.get("questions") or []
        sqls = ctx.get("sqls") or []
        n_q = len(questions)
        n_sql = len(sqls)
        # Top-3 question summaries (truncate each to ~120 chars to keep prompt tight)
        top_q = [str(q)[:120] for q in questions[:3]]
        topics_str = "; ".join(f"{i+1}) {q}" for i, q in enumerate(top_q)) if top_q else "(no questions yet)"

        prompt = (
            f"Build a comprehensive dashboard covering this conversation: "
            f"{n_q} questions asked. Topics: {topics_str}. "
            f"Use the data already queried in this chat ({n_sql} SQLs ran)."
        )

        persona = ctx.get("persona") or ""
        agent = DeepDashAgent(
            project_slug=req.project_slug,
            question=prompt,
            persona=persona,
            audience=req.audience,
            session_id=req.session_id,
            force_rebuild=True,
            signature_hash=signature_hash,
        )
    except Exception as e:
        logger.exception("from-chat/stream setup failed")
        return StreamingResponse(_err_stream(str(e)[:500]), media_type="text/event-stream")

    async def gen():
        try:
            async for event in agent.stream():
                yield f"data: {safe_dumps(event)}\n\n"
        except Exception as e:
            logger.exception("from-chat/stream agent failed")
            yield f"data: {safe_dumps({'type': 'error', 'detail': str(e)[:500]})}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.post("/deep-build")
async def deep_build_sync(req: DeepBuildRequest):
    """Non-streaming variant. Returns full result after all 9 stages complete."""
    if req.judge_model and req.gen_model and req.judge_model == req.gen_model:
        raise HTTPException(status_code=400, detail="judge_model must differ from gen_model (TACL self-bias)")
    agent = DeepDashAgent(
        project_slug=req.project_slug,
        question=req.question,
        persona=req.persona,
        audience=req.audience,
        n_panels=req.n_panels,
        gen_model=req.gen_model,
        judge_model=req.judge_model,
    )
    try:
        return _sanitize_json({"ok": True, **agent.run_sync()})
    except Exception as e:
        logger.exception("deep-build failed")
        return {"ok": False, "error": str(e)}


class DeepPatchRequest(BaseModel):
    spec: dict
    ops: list[dict]  # RFC 6902 JSON Patch operations


@router.post("/deep-patch")
async def deep_patch(req: DeepPatchRequest):
    """Apply RFC 6902 JSON Patch ops to a DeepDashSpec. Bumps spec_version.
    Never full regen — Grafana/Vizro pattern."""
    from dash.dashboards.agent import apply_patch
    try:
        patched = apply_patch(dict(req.spec), req.ops)
        return _sanitize_json({"ok": True, "spec": patched, "version": patched.get("spec_version", 1)})
    except Exception as e:
        logger.exception("deep-patch failed")
        return {"ok": False, "error": str(e)}


class RefineRequest(BaseModel):
    nl_command: str


@router.post("/{dashboard_id}/refine")
async def refine_dashboard(dashboard_id: str, req: RefineRequest):
    """NL → JSON Patch refine. Loads spec, derives ops via skl_dashboard_refiner,
    applies via RFC-6902 apply_patch, persists new spec, returns new spec +
    summary + streamed `panel_announcement` events for affected panels.
    """
    from dash.dashboards.agent import apply_patch, apply_refine_command, stage_panel_announce

    _ensure_table()
    eng = _get_engine()
    with eng.begin() as conn:
        row = conn.execute(text(
            "SELECT spec, session_id, project_slug, version, label "
            "FROM public.dash_dashboards_v2 WHERE id=:id"
        ), {"id": dashboard_id}).fetchone()
    if not row:
        raise HTTPException(404, "Dashboard not found")
    spec = row[0] if isinstance(row[0], dict) else json.loads(row[0])
    base_session_id = row[1]
    base_project_slug = row[2]
    base_version = int(row[3] or 1)
    base_label = row[4] or "Dashboard"

    try:
        refine = apply_refine_command(spec, req.nl_command)
    except Exception as e:
        logger.exception("apply_refine_command failed")
        raise HTTPException(500, f"refine failed: {e}")
    ops = refine.get("ops") or []
    summary = refine.get("summary") or ""

    if not ops:
        return {"ok": False, "error": "no patch ops generated", "summary": summary,
                "spec": spec, "ops": []}

    try:
        new_spec = apply_patch(dict(spec), ops)
    except Exception as e:
        logger.exception("apply_patch failed")
        raise HTTPException(500, f"apply_patch failed: {e}")

    # Identify affected panel ids from RFC-6902 paths like /panels/<idx>/...
    affected_idx: set[int] = set()
    for op in ops:
        path = op.get("path") or ""
        m = re.match(r"^/panels/(\d+)(?:/|$)", path) if (path) else None
        if m:
            try:
                affected_idx.add(int(m.group(1)))
            except Exception:
                pass

    panels = (new_spec.get("panels") if isinstance(new_spec, dict) else None) or []
    announcements: list[dict] = []
    for idx in sorted(affected_idx):
        if 0 <= idx < len(panels):
            p = panels[idx]
            try:
                ann = stage_panel_announce(_PanelView(p), row_count=0)
                announcements.append({"type": "panel_announcement", **ann})
            except Exception as e:
                logger.debug(f"announce failed for panel idx={idx}: {e}")

    # #1 — Refine creates a NEW versioned row (parent_id = original).
    slug = (new_spec.get("project_slug") if isinstance(new_spec, dict) else None) or base_project_slug or ""
    new_dashboard_id = f"deepdash_{uuid.uuid4().hex[:12]}"
    new_label = f"{base_label} (refined)"[:200]
    new_version = 1
    try:
        with eng.begin() as conn:
            if base_session_id:
                vrow = conn.execute(text(
                    "SELECT COALESCE(MAX(version),0) FROM public.dash_dashboards_v2 "
                    "WHERE project_slug=:p AND session_id=:s"
                ), {"p": slug, "s": base_session_id}).fetchone()
                new_version = int((vrow[0] if vrow else 0) or 0) + 1
            conn.execute(text(
                "INSERT INTO public.dash_dashboards_v2 "
                "(id, project_slug, spec, created_at, session_id, version, parent_id, label, signature_hash) "
                "VALUES (:id, :slug, CAST(:spec AS JSONB), NOW(), :sid, :ver, :par, :lbl, NULL)"
            ), {
                "id": new_dashboard_id,
                "slug": slug,
                "spec": json.dumps(new_spec, default=str),
                "sid": base_session_id,
                "ver": new_version,
                "par": dashboard_id,
                "lbl": new_label,
            })
    except Exception as e:
        logger.warning(f"refine persist (new version) failed: {e}")

    return {
        "ok": True,
        "spec": new_spec,
        "ops": ops,
        "summary": summary,
        "dashboard_id": new_dashboard_id,
        "parent_id": dashboard_id,
        "version": new_version,
        "panel_announcements": announcements,
    }


# Tiny attribute-access wrapper so dict panels can feed stage_panel_announce
class _PanelView:
    def __init__(self, d: dict):
        self._d = d if isinstance(d, dict) else {}
    def __getattr__(self, name):
        return self._d.get(name)


@router.post("/multi-agent/stream")
async def multi_agent_stream(req: GenerateRequest):
    """Stream multi-agent (Scout + Designer) dashboard build."""
    from dash.dashboards.agents.orchestrator import DashboardOrchestrator
    orch = DashboardOrchestrator(
        project_slug=req.project_slug,
        prompt=req.prompt or "",
        chat_context=req.chat_context,
        persona=req.persona or "",
        query_intent=getattr(req, "query_intent", "private") or "private",
    )
    async def gen():
        async for event in orch.stream():
            yield f"data: {safe_dumps(event)}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")


class DrillCellRequest(BaseModel):
    cell: dict
    project_slug: str
    persona: str = ""
    query_intent: str = "private"


@router.post("/drill-cell")
async def drill_cell(req: DrillCellRequest):
    """Run scout.drill on synthetic Finding from cell, return drill cells with data."""
    try:
        from dash.dashboards.agents.scout import drill
        from dash.dashboards.agents.designer import design
        from dash.dashboards.agents.contracts import Finding
        from dash.dashboards.runner import run_cell as exec_cell
        from db.session import get_project_readonly_engine

        cfg = req.cell.get("config", {}) or {}
        # WHY: drilling a network_grid leaks raw cross-store rows unless caller has network intent
        if req.cell.get("type") == "network_grid" and (req.query_intent or "private") == "private":
            return {"ok": False, "error": "network intent required to drill network_grid", "cells": []}
        try:
            from dash.tools.skill_refinery import set_request_context
            set_request_context(query_intent=req.query_intent or "private")
        except Exception:
            pass
        synthetic = Finding(
            id=req.cell.get("id", ""),
            headline=req.cell.get("title", ""),
            severity="medium",
            sql=cfg.get("sql", ""),
            data=[],
            domain_tags=cfg.get("domain_tags", []),
        )
        drills = await drill(req.project_slug, synthetic, req.persona)
        if not drills:
            return {"ok": True, "cells": []}
        decisions = await design(list(drills), req.persona, "")
        eng = get_project_readonly_engine(req.project_slug)
        cells_out = []
        for dec in decisions[:6]:
            cell = {
                "id": f"drill_{dec.finding_id}_{int(time.time()*1000)}",
                "type": dec.cell_type,
                "title": dec.title,
                "config": dec.config,
                "grid": [0, 0, 4, 3],
            }
            try:
                run_res = exec_cell(cell, eng, req.project_slug)
                cell["data"] = run_res
            except Exception as ce:
                cell["data"] = {"error": str(ce)}
            cells_out.append(cell)
        return {"ok": True, "cells": cells_out}
    except Exception as e:
        return {"ok": False, "error": str(e), "trace": _dbg_trace()}


@router.post("/multi-agent")
async def multi_agent_sync(req: GenerateRequest):
    from dash.dashboards.agents.orchestrator import DashboardOrchestrator
    orch = DashboardOrchestrator(
        project_slug=req.project_slug,
        prompt=req.prompt or "",
        chat_context=req.chat_context,
        persona=req.persona or "",
        query_intent=getattr(req, "query_intent", "private") or "private",
    )
    spec = orch.run_sync()
    return {"ok": True, "spec": spec, "findings": [f.model_dump() for f in orch.findings]}


@router.post("/save")
def save_dashboard(spec: DashboardSpec):
    _ensure_table()
    eng = _get_engine()
    payload = spec.model_dump(mode="json")
    with eng.begin() as conn:
        conn.execute(text(
            "INSERT INTO public.dash_dashboards_v2 (id, project_slug, spec, created_at) "
            "VALUES (:id, :slug, CAST(:spec AS JSONB), NOW()) "
            "ON CONFLICT (id) DO UPDATE SET project_slug=EXCLUDED.project_slug, spec=EXCLUDED.spec"
        ), {"id": spec.id, "slug": spec.project_slug, "spec": json.dumps(payload)})
    return {"ok": True, "id": spec.id}


@router.get("/list-all")
def list_all_dashboards():
    """List all v2 dashboards across all projects."""
    _ensure_table()
    eng = _get_engine()
    with eng.begin() as conn:
        rows = conn.execute(text(
            "SELECT id, project_slug, spec->>'title' AS title, created_at "
            "FROM public.dash_dashboards_v2 ORDER BY created_at DESC LIMIT 100"
        )).fetchall()
    return [
        {"id": r[0], "project_slug": r[1] or "", "title": r[2] or "Untitled", "created_at": str(r[3])}
        for r in rows
    ]


@router.get("/{dashboard_id}")
def get_dashboard(dashboard_id: str):
    _ensure_table()
    eng = _get_engine()
    with eng.begin() as conn:
        row = conn.execute(text(
            "SELECT spec, session_id, version, project_slug, label, created_at "
            "FROM public.dash_dashboards_v2 WHERE id=:id"
        ), {"id": dashboard_id}).fetchone()
    if not row:
        raise HTTPException(404, "Dashboard not found")
    spec = row[0] if isinstance(row[0], dict) else json.loads(row[0])
    # mirror panels -> cells for legacy renderers
    try:
        if isinstance(spec, dict):
            panels = spec.get("panels") or []
            cells = spec.get("cells") or []
            if panels and not cells:
                _cells = []
                for _p in panels:
                    _ptype = str(_p.get("panel_type") or "chart").lower()
                    _ct = "kpi" if _ptype == "kpi" else "insight" if _ptype in ("insight", "narrative") else "table" if _ptype == "table" else "chart"
                    _cells.append({
                        "id": _p.get("id") or f"c_{len(_cells)}",
                        "type": _ct,
                        "title": _p.get("title") or _p.get("name") or "Panel",
                        "sql": _p.get("sql") or "",
                        "chart_type": _p.get("chart_type") or "bar",
                        "chartType": _p.get("chart_type") or "bar",
                        "echarts_options": _p.get("options") or _p.get("echarts_options"),
                        "grid": _p.get("grid"),
                        "columns": _p.get("columns") or [],
                        "rows": _p.get("rows") or [],
                        "narrative": _p.get("narrative") or "",
                        "confidence": _p.get("confidence"),
                        "sources": _p.get("sources") or [],
                        "metadata": _p.get("metadata") or {},
                        "verified": _p.get("verified"),
                    })
                spec["cells"] = _cells
    except Exception:
        pass
    return {
        "spec": spec,
        "dashboard_id": dashboard_id,
        "session_id": row[1],
        "version": row[2],
        "project_slug": row[3],
        "label": row[4],
        "created_at": row[5].isoformat() if row[5] else None,
    }


@router.get("/list/{project_slug}")
def list_dashboards(project_slug: str):
    _ensure_table()
    eng = _get_engine()
    with eng.begin() as conn:
        rows = conn.execute(text(
            "SELECT id, spec->>'title' AS title, created_at "
            "FROM public.dash_dashboards_v2 WHERE project_slug=:slug "
            "ORDER BY created_at DESC"
        ), {"slug": project_slug}).fetchall()
    return [{"id": r[0], "title": r[1], "created_at": r[2].isoformat() if r[2] else None} for r in rows]


def _load_spec(dashboard_id: str) -> dict:
    _ensure_table()
    eng = _get_engine()
    with eng.begin() as conn:
        row = conn.execute(text("SELECT spec FROM public.dash_dashboards_v2 WHERE id=:id"),
                           {"id": dashboard_id}).fetchone()
    if not row:
        raise HTTPException(404, "Dashboard not found")
    return row[0] if isinstance(row[0], dict) else json.loads(row[0])


def _save_spec(dashboard_id: str, spec: dict) -> None:
    eng = _get_engine()
    with eng.begin() as conn:
        conn.execute(text(
            "UPDATE public.dash_dashboards_v2 SET spec=CAST(:spec AS JSONB) WHERE id=:id"
        ), {"id": dashboard_id, "spec": json.dumps(spec)})


class ShareRequest(BaseModel):
    public: bool


@router.post("/{dashboard_id}/share")
def share_dashboard(dashboard_id: str, req: ShareRequest):
    spec = _load_spec(dashboard_id)
    spec["is_public"] = bool(req.public)
    if req.public and not spec.get("share_token"):
        spec["share_token"] = secrets.token_hex(16)
    if not req.public:
        spec["share_token"] = ""
    _save_spec(dashboard_id, spec)
    token = spec.get("share_token", "")
    url = f"/api/dashboards/public/{token}" if token else ""
    return {"ok": True, "public": spec["is_public"], "token": token, "url": url}


@router.get("/public/{token}")
def get_public_dashboard(token: str):
    if not token:
        raise HTTPException(404, "Not found")
    _ensure_table()
    eng = _get_engine()
    with eng.begin() as conn:
        row = conn.execute(text(
            "SELECT spec FROM public.dash_dashboards_v2 "
            "WHERE spec->>'share_token'=:tok AND (spec->>'is_public')::bool = true"
        ), {"tok": token}).fetchone()
    if not row:
        raise HTTPException(404, "Not found")
    return row[0] if isinstance(row[0], dict) else json.loads(row[0])


@router.post("/{dashboard_id}/refresh")
def refresh_dashboard(dashboard_id: str):
    """Re-run all SQL cells in the spec, return spec with fresh data attached."""
    spec = _load_spec(dashboard_id)
    project_slug = spec.get("project_slug", "")
    refreshed = 0
    try:
        from dash.tools.skill_refinery import _get_engine as _eng
        eng = _eng()
        for cell in spec.get("cells", []):
            sql = (cell.get("config") or {}).get("sql")
            if not sql:
                continue
            try:
                with eng.begin() as conn:
                    rows = conn.execute(text(sql)).fetchall()
                    cell["config"]["data"] = [dict(r._mapping) for r in rows]
                    refreshed += 1
            except Exception as e:
                cell["config"]["error"] = str(e)
    except Exception as e:
        logger.warning(f"refresh failed: {e}")
    return {"ok": True, "spec": spec, "refreshed": refreshed, "project_slug": project_slug}


class MemoryLogRequest(BaseModel):
    project_slug: str
    action: str
    cell: dict | None = None
    spec_id: str | None = None
    finding_hash: str | None = None


@router.post("/memory/log")
def memory_log(req: MemoryLogRequest, request: Request):
    uid = _user_id(request) or "anonymous"
    ok = log_action(uid, req.project_slug, req.action, cell=req.cell, spec_id=req.spec_id)
    # Phase G — feed finding-retention loop. Backward compatible: missing hash → no-op.
    fhash = req.finding_hash
    if not fhash and isinstance(req.cell, dict):
        fhash = (req.cell.get("config") or {}).get("finding_hash")
    if fhash:
        try:
            from dash.dashboards.agents import memory_loop
            act = (req.action or "").lower()
            if act in ("keep", "kept", "save", "saved", "pin", "pinned"):
                memory_loop.record_keep(req.project_slug, fhash)
            elif act in ("delete", "deleted", "dismiss", "dismissed"):
                memory_loop.record_dismiss(req.project_slug, fhash)
        except Exception as e:
            logger.debug(f"memory_loop record failed: {e}")
    return {"ok": ok}


@router.get("/memory/preferences/{project_slug}")
def memory_preferences(project_slug: str, request: Request):
    uid = _user_id(request) or "anonymous"
    return {"ok": True, "preferences": get_preferences(uid, project_slug)}


# ─────────────────── by-session dashboard endpoints ───────────────────

def _normalize_panels_to_cells(spec: dict) -> dict:
    """Mirror spec.panels → spec.cells (legacy DashRenderer shape). Idempotent."""
    if not isinstance(spec, dict):
        return spec
    panels = spec.get("panels") or []
    if not panels or spec.get("cells"):
        return spec
    cells = []
    for p in panels:
        if not isinstance(p, dict):
            continue
        ptype = str(p.get("panel_type") or "chart").lower()
        ct = ("kpi" if ptype == "kpi"
              else "insight" if ptype in ("insight", "narrative")
              else "table" if ptype == "table"
              else "chart")
        cells.append({
            "id": p.get("panel_id") or f"p_{len(cells)+1}",
            "type": ct,
            "grid": p.get("grid") or [0, 0, 6, 3],
            "title": p.get("title") or "",
            "verified": bool(p.get("verified")),
            "source_metric": p.get("source_metric"),
            "config": {
                "chart_type": p.get("chart_type") or "bar",
                "echarts_options": p.get("options") or {},
                "narrative": p.get("narrative") or "",
                "confidence": p.get("confidence") or "medium",
                "sources": p.get("sources") or [],
                "headline": (p.get("title") or "") if ct in ("insight", "kpi") else None,
                "cause": (p.get("narrative") or "") if ct == "insight" else None,
            },
        })
    spec["cells"] = cells
    return spec


def _check_project_perm(request: Request, slug: str, role: str = "viewer") -> None:
    """Best-effort permission check mirroring app/customer_360.py pattern.
    Fail-soft if auth helpers unavailable (dev/test)."""
    try:
        from app.auth import get_current_user, check_project_permission, SUPER_ADMIN
        user = get_current_user(request)
        if not user:
            raise HTTPException(401, "auth required")
        res = check_project_permission(user, slug, role)
        if not res and user.get("username") != SUPER_ADMIN:
            raise HTTPException(403, "permission denied")
    except HTTPException:
        raise
    except Exception as _exc:
        logger.debug(f"auth helpers unavailable, skipping perm check: {_exc}")


@router.get("/by-session/{session_id}/latest")
async def get_latest_by_session(session_id: str, project_slug: str, request: Request):
    """Return latest dashboard version for a given (project_slug, session_id).
    Normalizes spec.panels → spec.cells for legacy renderer."""
    _check_project_perm(request, project_slug)
    from db.session import get_sql_engine as _get_ro
    try:
        with _get_ro().begin() as conn:
            row = conn.execute(text(
                "SELECT id, spec, version, created_at "
                "FROM public.dash_dashboards_v2 "
                "WHERE project_slug=:p AND session_id=:s "
                "ORDER BY version DESC LIMIT 1"
            ), {"p": project_slug, "s": session_id}).fetchone()
        if row is None:
            raise HTTPException(404, "no dashboard for session")
        spec = row[1]
        if isinstance(spec, str):
            try:
                spec = json.loads(spec)
            except Exception:
                spec = {}
        spec = _normalize_panels_to_cells(spec if isinstance(spec, dict) else {})
        return {
            "ok": True,
            "dashboard_id": row[0],
            "spec": spec,
            "version": int(row[2] or 1),
            "created_at": row[3].isoformat() if row[3] else None,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("get_latest_by_session failed")
        raise HTTPException(500, f"lookup failed: {str(e)[:200]}")


@router.get("/by-session/{session_id}")
async def list_by_session(session_id: str, project_slug: str, request: Request):
    """List all dashboard versions for (project_slug, session_id), newest first."""
    _check_project_perm(request, project_slug)
    from db.session import get_sql_engine as _get_ro
    try:
        with _get_ro().begin() as conn:
            rows = conn.execute(text(
                "SELECT id, version, created_at, label, "
                "COALESCE(jsonb_array_length(spec->'panels'), 0) AS n_panels "
                "FROM public.dash_dashboards_v2 "
                "WHERE project_slug=:p AND session_id=:s "
                "ORDER BY version DESC"
            ), {"p": project_slug, "s": session_id}).fetchall()
        return {
            "ok": True,
            "versions": [
                {
                    "dashboard_id": r[0],
                    "version": int(r[1] or 1),
                    "created_at": r[2].isoformat() if r[2] else None,
                    "label": r[3],
                    "n_panels": int(r[4] or 0),
                }
                for r in rows
            ],
        }
    except Exception as e:
        logger.exception("list_by_session failed")
        raise HTTPException(500, f"lookup failed: {str(e)[:200]}")


# ─────────────────── #4 DELETE endpoint ───────────────────

@router.delete("/{dashboard_id}")
async def delete_dashboard(dashboard_id: str, project_slug: str, request: Request):
    """Delete a dashboard by id. Repoints children's parent_id to NULL so
    lineage isn't fully broken. project_slug query param required to scope +
    block cross-tenant deletes. Requires editor role.
    """
    _check_project_perm(request, project_slug, role="editor")
    _ensure_table()
    eng = _get_engine()
    with eng.begin() as conn:
        row = conn.execute(text(
            "SELECT project_slug FROM public.dash_dashboards_v2 WHERE id=:id"
        ), {"id": dashboard_id}).fetchone()
        if not row:
            raise HTTPException(404, "Dashboard not found")
        if row[0] and row[0] != project_slug:
            raise HTTPException(403, "Project mismatch")
        conn.execute(text(
            "UPDATE public.dash_dashboards_v2 SET parent_id=NULL WHERE parent_id=:id"
        ), {"id": dashboard_id})
        conn.execute(text(
            "DELETE FROM public.dash_dashboards_v2 WHERE id=:id"
        ), {"id": dashboard_id})
    return {"ok": True, "deleted": dashboard_id}


class DashChatRequest(BaseModel):
    question: str
    history: list[dict] = []


@router.post("/{dashboard_id}/chat")
async def chat_with_dashboard(dashboard_id: str, req: DashChatRequest, request: Request):
    _ensure_table()
    eng = _get_engine()
    with eng.begin() as conn:
        row = conn.execute(text(
            "SELECT spec, project_slug FROM public.dash_dashboards_v2 WHERE id=:id"
        ), {"id": dashboard_id}).fetchone()
    if not row:
        raise HTTPException(404, "Dashboard not found")
    spec = row[0] if isinstance(row[0], dict) else json.loads(row[0])
    project_slug = row[1]
    panels = spec.get("panels") or spec.get("cells") or []
    context_lines = []
    context_lines.append(f"Dashboard with {len(panels)} panels:")
    for i, p in enumerate(panels[:20]):
        title = p.get("title") or p.get("name") or f"Panel {i+1}"
        ptype = p.get("panel_type") or p.get("type") or "chart"
        narrative = p.get("narrative") or ""
        rows = p.get("rows") or []
        cols = p.get("columns") or []
        line = f"\n[Panel {i+1}] {title} ({ptype})"
        if narrative:
            line += f"\n  narrative: {narrative[:300]}"
        if rows and cols:
            line += f"\n  data ({len(rows)} rows, columns: {', '.join(str(c) for c in cols[:10])}):"
            for r in rows[:5]:
                try:
                    row_str = " | ".join(f"{k}={v}" for k, v in list(r.items())[:8])
                    line += f"\n    {row_str}"
                except Exception:
                    pass
        context_lines.append(line)
    if spec.get("narrative"):
        nar = spec["narrative"]
        text_nar = nar.get("text") if isinstance(nar, dict) else str(nar)
        if text_nar:
            context_lines.insert(1, f"Executive narrative: {text_nar[:500]}")
    history_str = ""
    for h in (req.history or [])[-6:]:
        role = h.get("role", "user")
        content = (h.get("content") or "")[:500]
        history_str += f"\n{role.upper()}: {content}"
    system_prompt = (
        f"You are a data analyst answering questions about a specific dashboard. "
        f"Project: {project_slug}. Use ONLY the dashboard's panel data + narratives below. "
        f"If the answer is not in the dashboard, say so plainly. Cite panel numbers when "
        f"referencing data (e.g. 'Per Panel 3, …'). Be concise (2-4 sentences)."
    )
    full_prompt = (
        system_prompt + "\n\n" +
        "DASHBOARD CONTEXT:\n" + "\n".join(context_lines)[:6000] + "\n\n"
        + (f"PRIOR CHAT:{history_str}\n\n" if history_str else "")
        + f"USER QUESTION: {req.question}\n\nANSWER:"
    )
    try:
        from dash.settings import training_llm_call
        answer = training_llm_call(full_prompt, "chat") or ""
        if not answer:
            answer = "I couldn't generate an answer. Try rephrasing."
    except Exception as e:
        logger.exception("dashboard chat failed")
        answer = f"Error: {str(e)[:200]}"
    import re as _re
    cited = []
    for m in _re.finditer(r'[Pp]anel\s+(\d+)', answer):
        try:
            n = int(m.group(1))
            if 1 <= n <= len(panels):
                cited.append(n)
        except Exception:
            pass
    cited = sorted(set(cited))
    return {"answer": answer, "cited_panels": cited}
