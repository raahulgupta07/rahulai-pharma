"""HTTP endpoints for the self-learning cycle orchestrator.

All endpoints sit behind the AuthMiddleware (no SKIP_PATHS additions).

  POST /api/learning/cycle/{project_slug}   -- SSE stream of a project cycle
  POST /api/learning/cycle/central          -- SSE stream of the central cycle
  POST /api/projects/{slug}/learning/run    -- on-demand cycle (background thread)
  GET  /api/projects/{slug}/learning/runs   -- last N runs with logs
  GET  /api/learning/runs                    -- list past runs (super admin)
  GET  /api/learning/runs/{slug}             -- runs scoped to a project
  GET  /api/learning/questions/{slug}        -- current curiosity questions
  GET  /api/learning/hypotheses/{slug}       -- recent hypotheses
  GET  /api/learning/stats/{slug}            -- forgetting + cycle stats
"""
from __future__ import annotations

import asyncio
import json as _json
import logging
import threading
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import text

from dash.learning.cycle import LearningCycle, stream_cycle_sse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/learning", tags=["SelfLearning"])

# Second router for endpoints that live under /api/projects/{slug}/learning/*.
# Mounted alongside ``router`` in app/main.py.
projects_learning_router = APIRouter(tags=["SelfLearning"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_user(request: Request) -> dict:
    user = getattr(getattr(request, "state", None), "user", None)
    if not user:
        raise HTTPException(401, "Not authenticated")
    return user


def _require_super(user: dict):
    # admin tier OR super (day-to-day brain/training ops)
    if not user.get("is_admin"):
        raise HTTPException(403, "Admin required")


def _check_project(user: dict, slug: str, role: str = "viewer") -> dict:
    from app.auth import check_project_permission

    perm = check_project_permission(user, slug, required_role=role)
    if not perm:
        raise HTTPException(403, "Access denied")
    return perm


def _engine():
    from db.session import get_sql_engine

    return get_sql_engine()


# ---------------------------------------------------------------------------
# Cycle endpoints (SSE)
# ---------------------------------------------------------------------------

@router.post("/cycle/{project_slug}")
async def run_project_cycle(
    project_slug: str, request: Request, dry_run: bool = False
):
    """Start a self-learning cycle for one project. Streams SSE events."""
    user = _get_user(request)
    _check_project(user, project_slug, role="editor")

    try:
        body = await request.json()
    except Exception:
        body = {}
    max_q = int(body.get("max_questions", 20) or 20)
    run_decay = bool(body.get("run_decay", True))
    run_promotion = bool(body.get("run_promotion", False))

    try:
        from dash.settings import training_llm_call
    except Exception:
        training_llm_call = None  # type: ignore

    cycle = LearningCycle(
        project_slug=project_slug,
        llm_call_fn=training_llm_call if not dry_run else None,
        max_questions=max_q,
        run_decay=run_decay,
        run_promotion=run_promotion,
        dry_run=dry_run,
    )
    return StreamingResponse(
        stream_cycle_sse(cycle),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/cycle/central")
async def run_central_cycle(request: Request, dry_run: bool = False):
    """Start the central learning cycle (cross-project promotion)."""
    user = _get_user(request)
    _require_super(user)

    try:
        body = await request.json()
    except Exception:
        body = {}
    max_q = int(body.get("max_questions", 20) or 20)

    try:
        from dash.settings import training_llm_call
    except Exception:
        training_llm_call = None  # type: ignore

    cycle = LearningCycle(
        project_slug=None,
        llm_call_fn=training_llm_call if not dry_run else None,
        max_questions=max_q,
        run_decay=True,
        run_promotion=True,
        dry_run=dry_run,
    )
    return StreamingResponse(
        stream_cycle_sse(cycle),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Read endpoints
# ---------------------------------------------------------------------------

def _serialize_run(r) -> dict:
    return {
        "id": r[0],
        "project_slug": r[1],
        "cycle_num": r[2],
        "status": r[3],
        "questions_generated": r[4] or 0,
        "questions_answered": r[5] or 0,
        "hypotheses_formed": r[6] or 0,
        "hypotheses_verified": r[7] or 0,
        "hypotheses_failed": r[8] or 0,
        "facts_consolidated": r[9] or 0,
        "facts_promoted": r[10] or 0,
        "cost_usd": float(r[11] or 0.0),
        "duration_seconds": int(r[12] or 0) if r[12] is not None else None,
        "started_at": str(r[13]) if r[13] else None,
        "completed_at": str(r[14]) if r[14] else None,
        "error": r[15],
    }


_RUN_COLS = (
    "id, project_slug, cycle_num, status, "
    "questions_generated, questions_answered, hypotheses_formed, "
    "hypotheses_verified, hypotheses_failed, facts_consolidated, "
    "facts_promoted, cost_usd, duration_seconds, started_at, "
    "completed_at, error"
)


@router.get("/runs")
def list_runs(request: Request, limit: int = 50):
    """List recent self-learning runs across all projects (super admin)."""
    user = _get_user(request)
    _require_super(user)
    limit = max(1, min(int(limit or 50), 500))
    try:
        with _engine().connect() as conn:
            rows = conn.execute(text(
                f"SELECT {_RUN_COLS} FROM public.dash_self_learning_runs "
                f"ORDER BY started_at DESC NULLS LAST LIMIT :n"
            ), {"n": limit}).fetchall()
        return {"runs": [_serialize_run(r) for r in rows]}
    except Exception as e:
        logger.warning(f"list_runs failed: {e}")
        return {"runs": [], "error": str(e)[:200]}


@router.get("/runs/{slug}")
def list_project_runs(slug: str, request: Request, limit: int = 50):
    user = _get_user(request)
    _check_project(user, slug, role="viewer")
    limit = max(1, min(int(limit or 50), 500))
    try:
        with _engine().connect() as conn:
            rows = conn.execute(text(
                f"SELECT {_RUN_COLS} FROM public.dash_self_learning_runs "
                f"WHERE project_slug = :s "
                f"ORDER BY started_at DESC NULLS LAST LIMIT :n"
            ), {"s": slug, "n": limit}).fetchall()
        return {"runs": [_serialize_run(r) for r in rows]}
    except Exception as e:
        logger.warning(f"list_project_runs failed: {e}")
        return {"runs": [], "error": str(e)[:200]}


@router.get("/questions/{slug}")
def list_questions(slug: str, request: Request, limit: int = 100):
    user = _get_user(request)
    _check_project(user, slug, role="viewer")
    limit = max(1, min(int(limit or 100), 500))
    try:
        with _engine().connect() as conn:
            rows = conn.execute(text(
                "SELECT id, question, topic, reason, priority, status, "
                "       cycle_num, created_at, answered_at "
                "FROM public.dash_curiosity_questions "
                "WHERE project_slug = :s "
                "ORDER BY priority DESC, created_at DESC LIMIT :n"
            ), {"s": slug, "n": limit}).fetchall()
        return {
            "questions": [
                {
                    "id": r[0],
                    "question": r[1],
                    "topic": r[2],
                    "reason": r[3],
                    "priority": r[4],
                    "status": r[5],
                    "cycle_num": r[6],
                    "created_at": str(r[7]) if r[7] else None,
                    "answered_at": str(r[8]) if r[8] else None,
                }
                for r in rows
            ]
        }
    except Exception as e:
        logger.warning(f"list_questions failed: {e}")
        return {"questions": [], "error": str(e)[:200]}


@router.get("/hypotheses/{slug}")
def list_hypotheses(slug: str, request: Request, limit: int = 100):
    user = _get_user(request)
    _check_project(user, slug, role="viewer")
    limit = max(1, min(int(limit or 100), 500))
    try:
        with _engine().connect() as conn:
            rows = conn.execute(text(
                "SELECT id, statement, hypothesis_type, confidence, "
                "       verification_status, verified_by, "
                "       triangulation_count, promoted_to_central, "
                "       created_at, verified_at "
                "FROM public.dash_hypotheses "
                "WHERE project_slug = :s "
                "ORDER BY created_at DESC LIMIT :n"
            ), {"s": slug, "n": limit}).fetchall()
        return {
            "hypotheses": [
                {
                    "id": r[0],
                    "statement": r[1],
                    "type": r[2],
                    "confidence": float(r[3] or 0.0),
                    "verification_status": r[4],
                    "verified_by": r[5],
                    "triangulation_count": r[6] or 0,
                    "promoted_to_central": bool(r[7]) if r[7] is not None else False,
                    "created_at": str(r[8]) if r[8] else None,
                    "verified_at": str(r[9]) if r[9] else None,
                }
                for r in rows
            ]
        }
    except Exception as e:
        logger.warning(f"list_hypotheses failed: {e}")
        return {"hypotheses": [], "error": str(e)[:200]}


@router.get("/stats/{slug}")
def project_stats(slug: str, request: Request):
    user = _get_user(request)
    _check_project(user, slug, role="viewer")
    out: dict = {"slug": slug}

    # Cycle stats
    try:
        with _engine().connect() as conn:
            row = conn.execute(text(
                "SELECT COUNT(*), "
                "       COALESCE(SUM(questions_generated),0), "
                "       COALESCE(SUM(hypotheses_verified),0), "
                "       COALESCE(SUM(facts_consolidated),0), "
                "       COALESCE(SUM(facts_promoted),0), "
                "       COALESCE(SUM(cost_usd),0.0), "
                "       MAX(started_at) "
                "FROM public.dash_self_learning_runs "
                "WHERE project_slug = :s"
            ), {"s": slug}).fetchone()
            if row:
                out["cycles"] = {
                    "count": int(row[0] or 0),
                    "questions_generated_total": int(row[1] or 0),
                    "hypotheses_verified_total": int(row[2] or 0),
                    "facts_consolidated_total": int(row[3] or 0),
                    "facts_promoted_total": int(row[4] or 0),
                    "cost_usd_total": float(row[5] or 0.0),
                    "last_run_at": str(row[6]) if row[6] else None,
                }
    except Exception as e:
        out["cycles_error"] = str(e)[:200]

    # Forgetting stats (from forgetting.stats helper if available)
    try:
        from dash.learning.forgetting import stats as decay_stats

        out["forgetting"] = decay_stats()
    except Exception as e:
        out["forgetting_error"] = str(e)[:200]

    return out


# ---------------------------------------------------------------------------
# Daily learning scheduler endpoints (super-admin only)
# ---------------------------------------------------------------------------

@router.get("/scheduler/state")
def scheduler_state(request: Request):
    """Return current scheduler state (last run, next run, projects processed)."""
    user = _get_user(request)
    _require_super(user)
    from dash.learning.scheduler import get_state
    return get_state()


@router.post("/scheduler/trigger")
async def scheduler_trigger(request: Request):
    """Force a sweep right now. Returns updated state snapshot."""
    user = _get_user(request)
    _require_super(user)
    from dash.learning.scheduler import trigger_now
    return await trigger_now()


@router.post("/scheduler/disable")
def scheduler_disable(request: Request):
    """Pause the daily scheduler (loop continues but skips sweeps)."""
    user = _get_user(request)
    _require_super(user)
    from dash.learning.scheduler import disable
    disable()
    return {"enabled": False}


@router.post("/scheduler/enable")
def scheduler_enable(request: Request):
    """Re-enable the daily scheduler."""
    user = _get_user(request)
    _require_super(user)
    from dash.learning.scheduler import enable
    enable()
    return {"enabled": True}


# ---------------------------------------------------------------------------
# Agent IQ endpoints
# ---------------------------------------------------------------------------

@router.get("/iq/{slug}")
def get_iq(slug: str, request: Request, days: int = 30):
    """Return current agent_iq + history for a project."""
    user = _get_user(request)
    _check_project(user, slug, role="viewer")
    from dash.learning.agent_iq import compute, history
    current = compute(slug)
    series = history(slug, days=days)
    return {
        "slug": slug,
        "current": current.components,
        "history": series,
        "days": days,
    }


@router.get("/iq/central/all")
def get_iq_central(request: Request, days: int = 30):
    """Return central (cross-project) agent_iq + history. Super admin only."""
    user = _get_user(request)
    _require_super(user)
    from dash.learning.agent_iq import compute, history
    return {
        "current": compute(None).components,
        "history": history(None, days=days),
        "days": days,
    }


# ---------------------------------------------------------------------------
# Learning goals (human-editable per-project agent program)
# ---------------------------------------------------------------------------

@router.get("/goals/{slug}")
def get_goals(slug: str, request: Request):
    """Return the project's learning_goals.md content (creates from template if absent)."""
    user = _get_user(request)
    _check_project(user, slug, role="viewer")
    from dash.learning.goals import read_goals
    return {"slug": slug, "content": read_goals(slug)}


@router.post("/goals/{slug}")
async def post_goals(slug: str, request: Request):
    """Persist edited learning_goals.md content. Editor role required."""
    user = _get_user(request)
    _check_project(user, slug, role="editor")
    try:
        body = await request.json()
    except Exception:
        body = {}
    from dash.learning.goals import write_goals
    content = body.get("content", "") if isinstance(body, dict) else ""
    ok = write_goals(slug, content)
    return {"saved": ok}


@router.post("/goals/{slug}/derive")
async def post_goals_derive(slug: str, request: Request):
    """LLM auto-derive learning_goals.md from project signals. Editor required.

    Body: {force: bool} — overwrite even if user-edited.
    """
    user = _get_user(request)
    _check_project(user, slug, role="editor")
    try:
        body = await request.json()
    except Exception:
        body = {}
    force = bool(body.get("force", False)) if isinstance(body, dict) else False
    from dash.learning.goals_deriver import derive_goals
    return derive_goals(slug, force=force)


# ---------------------------------------------------------------------------
# Per-step runner — execute single training step without full TRAIN ALL
# ---------------------------------------------------------------------------

_STEP_LIST = ["goals", "scope", "kg", "persona", "relationships", "evolved_instructions"]


@router.get("/steps/{slug}")
def list_steps(slug: str, request: Request):
    """List individually-runnable training steps for the project."""
    user = _get_user(request)
    _check_project(user, slug, role="viewer")
    return {
        "steps": [
            {"id": "goals", "label": "Learning Goals", "desc": "Auto-derive learning_goals.md from persona+tables+docs+KG+bad-feedback"},
            {"id": "scope", "label": "Scope Guardrail", "desc": "Re-derive allowed/denied topics for refusal layer"},
            {"id": "kg", "label": "Knowledge Graph", "desc": "Re-extract SPO triples + entity standardization across tables/docs/facts"},
            {"id": "persona", "label": "Persona", "desc": "Re-generate persona from current tables + rules"},
            {"id": "relationships", "label": "Relationships", "desc": "LLM rediscover joins/foreign keys across tables"},
            {"id": "evolved_instructions", "label": "Evolved Instructions", "desc": "Run auto-evolve pass over recent learnings → bump v_N"},
        ]
    }


@router.post("/steps/{slug}/run")
async def run_step(slug: str, request: Request):
    """Run a single training step in isolation. Editor role required.

    Body: {step: "goals"|"scope"|"kg"|"persona"|"relationships"|"evolved_instructions", force?: bool}
    """
    import logging
    user = _get_user(request)
    _check_project(user, slug, role="editor")
    try:
        body = await request.json()
    except Exception:
        body = {}
    step = (body or {}).get("step", "").strip()
    force = bool((body or {}).get("force", False))

    if step not in _STEP_LIST:
        return {"ok": False, "step": step, "reason": f"unknown step. valid: {_STEP_LIST}"}

    log = logging.getLogger(__name__)
    try:
        if step == "goals":
            from dash.learning.goals_deriver import derive_goals
            r = derive_goals(slug, force=force)
            return {"ok": r.get("ok", True), "step": step, "result": r}

        if step == "scope":
            from dash.scope_deriver import derive_scope
            from dash.feature_config import set_scope
            derived = derive_scope(slug)
            set_scope(slug, derived, mark_auto=True)
            return {"ok": True, "step": step, "result": {
                "topics": len(derived.get("topics", [])),
                "denied": len(derived.get("denied_intents", [])),
                "entities": len(derived.get("core_entities", [])),
            }}

        if step == "kg":
            from dash.tools.knowledge_graph import build_knowledge_graph
            res = build_knowledge_graph(slug)
            return {"ok": True, "step": step, "result": res if isinstance(res, dict) else {"summary": str(res)[:300]}}

        if step == "persona":
            from app.upload import _llm_generate_persona
            from sqlalchemy import text as _t
            from db import db_url
            from sqlalchemy import create_engine
            from sqlalchemy.pool import NullPool
            eng = create_engine(db_url, poolclass=NullPool)
            try:
                with eng.connect() as conn:
                    tm = conn.execute(_t("SELECT table_name, columns_metadata, sample_data, business_purpose FROM dash_table_metadata WHERE project_slug=:s"), {"s": slug}).mappings().all()
                    rs = conn.execute(_t("SELECT name, description FROM dash_rules_db WHERE project_slug=:s"), {"s": slug}).mappings().all()
                tables_metadata = [dict(r) for r in tm]
                rules_list = [dict(r) for r in rs]
            finally:
                eng.dispose()
            persona = _llm_generate_persona(slug, tables_metadata, rules_list)
            if persona:
                return {"ok": True, "step": step, "result": {"persona_prompt_len": len(persona.get("persona_prompt", "")), "domain_terms": persona.get("domain_terms", [])[:6]}}
            return {"ok": False, "step": step, "reason": "persona generator returned None"}

        if step == "relationships":
            from app.upload import _discover_relationships
            _discover_relationships(slug)
            return {"ok": True, "step": step, "result": {"status": "completed"}}

        if step == "evolved_instructions":
            from dash.tools.auto_evolve import auto_evolve_if_needed, force_evolve
            try:
                res = force_evolve(slug)
            except Exception:
                res = auto_evolve_if_needed(slug, force=True)
            return {"ok": True, "step": step, "result": res if isinstance(res, dict) else {"summary": str(res)[:300]}}

    except Exception as e:
        log.exception(f"step {step} failed for {slug}: {e}")
        return {"ok": False, "step": step, "reason": str(e)[:300]}

    return {"ok": False, "step": step, "reason": "unhandled"}


# ---------------------------------------------------------------------------
# Cost ceiling endpoints
# ---------------------------------------------------------------------------

def _asdict_or_dict(obj):
    """Convert dataclass to dict, or pass-through dict-like objects."""
    try:
        from dataclasses import asdict, is_dataclass
        if is_dataclass(obj):
            return asdict(obj)
    except Exception:
        pass
    if isinstance(obj, dict):
        return obj
    return getattr(obj, "__dict__", {}) or {}


@router.get("/lineage/roots/{slug}")
def get_root_trees_endpoint(slug: str, request: Request, limit: int = 20):
    """List root hypotheses (no parent) for a project, with child counts."""
    user = _get_user(request)
    _check_project(user, slug, role="viewer")
    from dash.learning.lineage import get_root_trees
    return {"slug": slug, "roots": get_root_trees(slug, limit=limit)}


@router.get("/lineage/{hypothesis_id}")
def get_lineage_endpoint(hypothesis_id: int, request: Request):
    """Walk parent chain up + child tree down for a hypothesis."""
    _get_user(request)
    from dash.learning.lineage import get_lineage
    return get_lineage(hypothesis_id)


@router.get("/cost/{slug}")
def get_cost(slug: str, request: Request):
    """Return current cost status + 7-day history for project."""
    user = _get_user(request)
    _check_project(user, slug, role="viewer")
    from dash.learning.cost_guard import get_status, history_7d
    return {
        "slug": slug,
        "status": _asdict_or_dict(get_status(slug)),
        "history_7d": history_7d(slug),
    }


@router.get("/digests/{slug}")
def list_digests(slug: str, request: Request, limit: int = 20):
    """List recent end-of-cycle digests for a project."""
    user = _get_user(request)
    _check_project(user, slug, role="viewer")
    from dash.learning.digest import list_recent
    return {"slug": slug, "digests": list_recent(slug, limit=limit)}


@router.post("/decay")
def trigger_decay(request: Request):
    """Lightweight forgetting-curve decay job (no LLM).

    Designed for the dash-learning-decay CronJob. Super admin only.
    """
    user = _get_user(request)
    _require_super(user)
    from dash.learning.forgetting import daily_decay_job
    result = daily_decay_job()
    return {
        "status": "ok",
        "decayed": getattr(result, "decayed_count", 0),
        "archived": getattr(result, "archived_count", 0),
        "unarchived": getattr(result, "unarchived_count", 0),
        "deleted": getattr(result, "deleted_count", 0),
    }


@router.get("/projects/optin")
def list_optin_projects(request: Request):
    """List slugs of projects opted in to learning. Super admin only.

    Used by the K8S learning CronJobs to enumerate cycle targets.
    """
    user = _get_user(request)
    _require_super(user)
    try:
        with _engine().connect() as conn:
            rows = conn.execute(text(
                "SELECT slug FROM public.dash_projects "
                "WHERE COALESCE(contribute_to_central, TRUE) = TRUE "
                "   OR COALESCE(receive_from_central, TRUE) = TRUE "
                "ORDER BY slug"
            )).fetchall()
        return {"slugs": [r[0] for r in rows]}
    except Exception as e:
        logger.warning(f"list_optin_projects failed: {e}")
        return {"slugs": [], "error": str(e)[:200]}


# ---------------------------------------------------------------------------
# Domain detection endpoints
# ---------------------------------------------------------------------------

@router.get("/domain/{project_slug}/{source_id}")
def get_domain(project_slug: str, source_id: int, request: Request):
    """Read detected domain for a source."""
    _get_user(request)
    from dash.learning.domain_detector import load
    data = load(project_slug, source_id)
    if data is None:
        return {"detected": False, "primary": None, "secondaries": [], "confidence": 0.0}
    return {
        "detected": True,
        "primary": data.get("primary", "generic"),
        "secondaries": data.get("secondaries", []),
        "confidence": data.get("confidence", 0.0),
        "all_scores": data.get("all_scores", [])[:6],
        "manual_override": data.get("manual_override", False),
    }


@router.post("/domain/{project_slug}/{source_id}/override")
async def override_domain(project_slug: str, source_id: int, request: Request):
    """Manually override detected domain."""
    user = _get_user(request)
    _check_project(user, project_slug, role="editor")

    try:
        body = await request.json()
    except Exception:
        body = {}
    body = body or {}

    from dash.learning.domain_detector import all_domains
    primary = body.get("primary", "generic")
    secondaries = body.get("secondaries", []) or []
    if primary not in all_domains():
        raise HTTPException(400, f"unknown domain: {primary}")
    secondaries = [s for s in secondaries if s in all_domains() and s != primary]

    import json
    from pathlib import Path
    p = Path("knowledge") / project_slug / f"source_{source_id}" / "domain.json"
    p.parent.mkdir(parents=True, exist_ok=True)

    existing = {}
    if p.exists():
        try:
            existing = json.loads(p.read_text())
        except Exception:
            pass

    p.write_text(json.dumps({
        "primary": primary,
        "secondaries": secondaries,
        "confidence": 1.0,
        "all_scores": existing.get("all_scores", []),
        "manual_override": True,
        "overridden_by": user.get("user_id") if isinstance(user, dict) else None,
    }, indent=2))

    return {"saved": True, "primary": primary, "secondaries": secondaries}


@router.post("/domain/{project_slug}/{source_id}/redetect")
def redetect_domain(project_slug: str, source_id: int, request: Request):
    """Re-run domain detector (forces fresh detection)."""
    user = _get_user(request)
    _check_project(user, project_slug, role="editor")
    from dash.learning.domain_detector import detect
    detection = detect(project_slug, source_id)
    return {
        "primary": detection.primary,
        "secondaries": detection.secondaries,
        "confidence": detection.confidence,
    }


@router.get("/domain/list")
def list_domains(request: Request):
    """List all known domains for dropdown."""
    _get_user(request)
    from dash.learning.domain_detector import all_domains
    return {"domains": all_domains()}


# ---------------------------------------------------------------------------
# On-demand learning runs (UI-triggered, background thread)
# ---------------------------------------------------------------------------

# Step name -> 1-based ordinal in the 8-step cycle (curiosity → researcher →
# hypothesis → verifier → consolidator → forgetter → promotion → digest).
_STEP_ORDER = {
    "curiosity": 1,
    "research": 2,
    "researcher": 2,
    "hypothesize": 3,
    "hypothesis": 3,
    "verify": 4,
    "verifier": 4,
    "consolidate": 5,
    "consolidator": 5,
    "forgetting": 6,
    "forgetter": 6,
    "promotion": 7,
    "agent_iq": 7,
    "digest": 8,
    # "cycle" intentionally omitted — it's a meta event (start/done/error)
    # and shouldn't override the in-flight step counter.
}
_TOTAL_STEPS = 8


def _raw_engine():
    """Engine WITHOUT the public-schema write guard.

    The cached ``get_sql_engine()`` rejects INSERT public.* via a SQLAlchemy
    event listener. Self-learning rows live in ``public.dash_self_learning_runs``
    by design (cross-project, central pool), so on-demand writes bypass the
    Engineer-only guard with a NullPool engine. Identical pattern to the
    bootstrap engine used during schema creation.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool
    from db.url import build_db_url
    return create_engine(build_db_url(), poolclass=NullPool)


def _ondemand_insert_run(slug: str, focus: Optional[str]) -> int:
    """Insert a fresh run row in 'running' state and return its id."""
    eng = _raw_engine()
    with eng.connect() as conn:
        # cycle_num: next per-project
        row = conn.execute(text(
            "SELECT COALESCE(MAX(cycle_num), 0) + 1 FROM public.dash_self_learning_runs "
            "WHERE project_slug IS NOT DISTINCT FROM :s"
        ), {"s": slug}).fetchone()
        cycle_num = int(row[0]) if row else 1
        rid_row = conn.execute(text(
            "INSERT INTO public.dash_self_learning_runs "
            "(project_slug, cycle_num, status, started_at, logs, "
            " current_step, step_index, total_steps, focus, metadata) "
            "VALUES (:s, :n, 'running', NOW(), '[]'::jsonb, "
            " 'queued', 0, :t, :focus, CAST(:meta AS JSONB)) "
            "RETURNING id"
        ), {
            "s": slug,
            "n": cycle_num,
            "t": _TOTAL_STEPS,
            "focus": focus,
            "meta": _json.dumps({"trigger": "on_demand"}),
        }).fetchone()
        conn.commit()
        return int(rid_row[0])


# Cooperative stop flags for in-flight on-demand runs (run_id -> True).
# In multi-worker uvicorn this dict only covers same-process runs;
# _is_cancel_requested() falls back to a DB poll for cross-worker stops.
_STOP_FLAGS: dict[int, bool] = {}


def _is_cancel_requested(run_id: int) -> bool:
    """Cross-worker cancel check via dash_self_learning_runs.status='cancelling'."""
    try:
        eng = _raw_engine()
        with eng.connect() as conn:
            row = conn.execute(text(
                "SELECT status FROM public.dash_self_learning_runs WHERE id=:i"
            ), {"i": run_id}).fetchone()
            return bool(row and str(row[0]) == "cancelling")
    except Exception:
        return False


def _ondemand_log(run_id: int, step: str, msg: str, **extra) -> None:
    """Append a log entry and bump current_step/step_index in one UPDATE."""
    try:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "step": step,
            "msg": msg,
        }
        if extra:
            entry.update({k: v for k, v in extra.items() if v is not None})
        sidx = _STEP_ORDER.get((step or "").lower())
        eng = _raw_engine()
        with eng.connect() as conn:
            if sidx is not None:
                conn.execute(text(
                    "UPDATE public.dash_self_learning_runs SET "
                    " logs = COALESCE(logs, '[]'::jsonb) || CAST(:e AS JSONB), "
                    " current_step = :s, "
                    " step_index = GREATEST(COALESCE(step_index, 0), :i) "
                    "WHERE id = :id"
                ), {"e": _json.dumps([entry]), "s": step, "i": sidx, "id": run_id})
            else:
                conn.execute(text(
                    "UPDATE public.dash_self_learning_runs SET "
                    " logs = COALESCE(logs, '[]'::jsonb) || CAST(:e AS JSONB) "
                    "WHERE id = :id"
                ), {"e": _json.dumps([entry]), "id": run_id})
            conn.commit()
    except Exception as e:
        logger.warning(f"on-demand log update failed: {e}")


def _ondemand_finalize(run_id: int, *, status: str, summary: Optional[str], error: Optional[str]) -> None:
    try:
        eng = _raw_engine()
        with eng.connect() as conn:
            conn.execute(text(
                "UPDATE public.dash_self_learning_runs SET "
                " status = :st, "
                " completed_at = NOW(), "
                " duration_seconds = EXTRACT(EPOCH FROM NOW() - started_at)::int, "
                " summary = COALESCE(:sum, summary), "
                " error = COALESCE(:err, error) "
                "WHERE id = :id"
            ), {"st": status, "sum": summary, "err": error, "id": run_id})
            conn.commit()
    except Exception as e:
        logger.warning(f"on-demand finalize failed: {e}")


def _ondemand_run_thread(slug: str, run_id: int, force: bool, focus: Optional[str]) -> None:
    """Background thread entry: drives the LearningCycle async generator and
    pipes every event into the run's logs column. Cost cap respected via
    ``set_llm_project(slug)``.
    """
    try:
        from dash.settings import set_llm_project, training_llm_call
    except Exception:
        set_llm_project = None  # type: ignore
        training_llm_call = None  # type: ignore

    set_llm_project and set_llm_project(slug)
    summary: Optional[str] = None
    final_status = "completed"
    final_error: Optional[str] = None
    try:
        _ondemand_log(run_id, "cycle", f"started focus={focus or 'none'} force={force}")

        cycle = LearningCycle(
            project_slug=slug,
            llm_call_fn=training_llm_call,
            run_decay=True,
            run_promotion=False,  # don't auto-promote from a manual UI kick
            dry_run=False,
        )

        async def _drive():
            nonlocal summary
            check_n = 0
            async for evt in cycle.run():
                # Cross-worker stop check: poll DB every event (cheap)
                check_n += 1
                if _STOP_FLAGS.get(run_id) or _is_cancel_requested(run_id):
                    _ondemand_log(run_id, "cycle", "[cancelled] stop requested by user")
                    break
                step = str(evt.get("step", "")) or "cycle"
                status = str(evt.get("status", "")) or ""
                msg = str(evt.get("message", "")) or ""
                _ondemand_log(
                    run_id, step,
                    f"[{status}] {msg}" if status else msg,
                    raw_status=status,
                )
                if step == "cycle" and status == "done" and msg:
                    summary = msg
                if step == "cycle" and status == "error":
                    nonlocal final_error
                    final_error = msg

        asyncio.run(_drive())
        if _STOP_FLAGS.pop(run_id, False):
            final_status = "cancelled"

        # Pull cost / counters back from the row (cycle._update_run_row writes
        # them on its own). Use them to enrich summary if missing.
        if not summary:
            try:
                with _engine().connect() as conn:
                    row = conn.execute(text(
                        "SELECT questions_generated, hypotheses_verified, "
                        "       facts_consolidated, facts_promoted, cost_usd "
                        "FROM public.dash_self_learning_runs WHERE id = :id"
                    ), {"id": run_id}).fetchone()
                    if row:
                        summary = _json.dumps({
                            "questions": int(row[0] or 0),
                            "verified": int(row[1] or 0),
                            "consolidated": int(row[2] or 0),
                            "promoted": int(row[3] or 0),
                            "cost_usd": float(row[4] or 0.0),
                        })
            except Exception:
                pass

        if final_error:
            final_status = "failed"
        _ondemand_log(run_id, "cycle", f"finished status={final_status}")
    except Exception as e:
        logger.exception(f"on-demand cycle crashed: {e}")
        final_status = "failed"
        final_error = str(e)[:500]
        _ondemand_log(run_id, "cycle", f"crashed: {final_error}")
    finally:
        try:
            set_llm_project and set_llm_project(None)
        except Exception:
            pass
        _ondemand_finalize(run_id, status=final_status, summary=summary, error=final_error)


@projects_learning_router.post("/api/projects/{slug}/learning/run", include_in_schema=True)
async def projects_learning_run(slug: str, request: Request):
    """Kick a self-learn cycle on-demand. Returns run_id immediately.

    Body (optional): {"force": bool, "focus": str}. Editor role required.
    """
    user = _get_user(request)
    _check_project(user, slug, role="editor")

    try:
        body = await request.json()
    except Exception:
        body = {}
    body = body or {}
    force = bool(body.get("force", False))
    focus = body.get("focus") if isinstance(body.get("focus"), str) else None

    try:
        run_id = _ondemand_insert_run(slug, focus)
    except Exception as e:
        logger.warning(f"insert run row failed: {e}")
        raise HTTPException(500, f"could not create run: {str(e)[:120]}")

    th = threading.Thread(
        target=_ondemand_run_thread,
        args=(slug, run_id, force, focus),
        name=f"self-learn-{slug}-{run_id}",
        daemon=True,
    )
    th.start()

    return {
        "run_id": run_id,
        "status": "running",
        "project_slug": slug,
        "focus": focus,
        "force": force,
    }


def _serialize_ondemand_run(r) -> dict:
    return {
        "id": r[0],
        "status": r[1],
        "started_at": str(r[2]) if r[2] else None,
        "finished_at": str(r[3]) if r[3] else None,
        "duration_s": int(r[4]) if r[4] is not None else None,
        "current_step": r[5],
        "step_index": int(r[6] or 0),
        "total_steps": int(r[7] or _TOTAL_STEPS),
        "logs": r[8] if r[8] is not None else [],
        "summary": r[9],
        "cost_usd": float(r[10] or 0.0),
        "hypotheses_promoted": int(r[11] or 0),
        "memories_forgotten": int(r[12] or 0),
        "error": r[13],
    }


_ONDEMAND_COLS = (
    "id, status, started_at, completed_at, duration_seconds, "
    "current_step, step_index, total_steps, logs, summary, "
    "cost_usd, facts_promoted, COALESCE(memories_forgotten, 0), error"
)


@projects_learning_router.get("/api/projects/{slug}/learning/runs", include_in_schema=True)
def projects_learning_runs(slug: str, request: Request, limit: int = 10):
    """Return last N self-learning runs (newest first) for a project."""
    user = _get_user(request)
    _check_project(user, slug, role="viewer")
    limit = max(1, min(int(limit or 10), 100))
    try:
        with _engine().connect() as conn:
            rows = conn.execute(text(
                f"SELECT {_ONDEMAND_COLS} "
                f"FROM public.dash_self_learning_runs "
                f"WHERE project_slug = :s "
                f"ORDER BY started_at DESC NULLS LAST LIMIT :n"
            ), {"s": slug, "n": limit}).fetchall()
        return {
            "slug": slug,
            "runs": [_serialize_ondemand_run(r) for r in rows],
        }
    except Exception as e:
        logger.warning(f"projects_learning_runs failed: {e}")
        return {"slug": slug, "runs": [], "error": str(e)[:200]}


@projects_learning_router.post("/api/projects/{slug}/learning/runs/{run_id}/stop", include_in_schema=True)
def projects_learning_run_stop(slug: str, run_id: int, request: Request):
    """Cooperatively cancel an in-flight self-learn cycle. Editor role required.

    Sets a stop flag the bg thread checks before each cycle event. The thread
    finalizes with status='cancelled' on the next checkpoint.
    """
    user = _get_user(request)
    _check_project(user, slug, role="editor")
    try:
        eng = _raw_engine()
        with eng.begin() as conn:
            row = conn.execute(text(
                "SELECT status FROM public.dash_self_learning_runs "
                "WHERE id=:i AND project_slug=:s"
            ), {"i": run_id, "s": slug}).fetchone()
            if not row:
                raise HTTPException(404, "run not found")
            if row[0] not in ("running", "pending"):
                return {"ok": True, "run_id": run_id, "status": row[0], "noop": True}
            conn.execute(text(
                "UPDATE public.dash_self_learning_runs SET status='cancelling' "
                "WHERE id=:i"
            ), {"i": run_id})
        _STOP_FLAGS[run_id] = True
        _ondemand_log(run_id, "cycle", "[stop_requested] user clicked STOP")
        return {"ok": True, "run_id": run_id, "status": "cancelling"}
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"stop run failed: {e}")
        raise HTTPException(500, str(e)[:200])


@router.post("/cost/{slug}/cap")
async def set_cap(slug: str, request: Request):
    """Update per-project daily cost cap. Editor role required."""
    user = _get_user(request)
    _check_project(user, slug, role="editor")
    try:
        body = await request.json()
    except Exception:
        body = {}
    cap = float((body or {}).get("daily_cost_cap_usd", 1.0))
    from sqlalchemy import text
    from db.session import get_sql_engine
    with get_sql_engine().connect() as conn:
        conn.execute(text(
            "UPDATE public.dash_projects SET daily_cost_cap_usd = :c WHERE slug = :s"
        ), {"c": cap, "s": slug})
        conn.commit()
    return {"slug": slug, "daily_cost_cap_usd": cap}
