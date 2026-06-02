"""Dash-OS Phase 6 — Evals CRUD + run + baseline + secret-leak audit."""
from __future__ import annotations

import json as _json
import logging
import secrets
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/evals", tags=["evals"])


def _get_engine():
    try:
        from db.session import get_sql_engine
        return get_sql_engine()
    except Exception:
        try:
            from db import get_sql_engine  # type: ignore
            return get_sql_engine()
        except Exception:
            return None


def _user_dep():
    try:
        from app.auth import _get_user
        return _get_user
    except Exception:
        def _noop():
            return {"id": 0, "username": "anonymous", "is_super_admin": False}
        return _noop


_get_user = _user_dep()


class SuiteIn(BaseModel):
    project_slug: Optional[str] = None
    name: str
    description: Optional[str] = None
    layer: str  # 'smoke' | 'reliability' | 'llm_judge' | 'regression'
    target_agent: Optional[str] = None


class CaseIn(BaseModel):
    suite_id: str
    name: str
    input_prompt: str
    expected_output: Optional[str] = None
    expected_tool_calls: List[str] = []
    judge_prompt: Optional[str] = None
    max_latency_ms: Optional[int] = None


@router.get("/suites")
def list_suites(
    project_slug: Optional[str] = Query(None),
    layer: Optional[str] = Query(None),
    user=Depends(_get_user),
):
    eng = _get_engine()
    if eng is None:
        return {"suites": []}
    from sqlalchemy import text
    with eng.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT s.id, s.name, s.description, s.layer, s.target_agent,
                       s.project_slug, s.is_builtin, s.enabled, s.created_at,
                       (SELECT COUNT(*) FROM dash.dash_eval_cases c WHERE c.suite_id = s.id) AS case_count,
                       (SELECT pass_rate FROM dash.dash_eval_baselines b
                          WHERE b.suite_id = s.id ORDER BY set_at DESC LIMIT 1) AS baseline_pass_rate
                FROM dash.dash_eval_suites s
                WHERE (:ps IS NULL OR s.project_slug = :ps OR s.project_slug IS NULL)
                  AND (:l IS NULL OR s.layer = :l)
                ORDER BY s.is_builtin DESC, s.name
                """
            ),
            {"ps": project_slug, "l": layer},
        ).mappings().all()
    return {"suites": [dict(r) for r in rows]}


@router.post("/suites")
def create_suite(body: SuiteIn, user=Depends(_get_user)):
    if body.layer not in ("smoke", "reliability", "llm_judge", "regression"):
        raise HTTPException(400, "invalid layer")
    eng = _get_engine()
    if eng is None:
        raise HTTPException(503, "db unavailable")
    sid = "es_" + secrets.token_hex(4)
    from sqlalchemy import text
    with eng.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO dash.dash_eval_suites
                  (id, project_slug, name, description, layer, target_agent)
                VALUES (:id, :ps, :nm, :ds, :l, :ta)
                ON CONFLICT (name, project_slug) DO UPDATE
                  SET description = EXCLUDED.description, layer = EXCLUDED.layer,
                      target_agent = EXCLUDED.target_agent, updated_at = now()
                """
            ),
            {"id": sid, "ps": body.project_slug, "nm": body.name,
             "ds": body.description, "l": body.layer, "ta": body.target_agent},
        )
    return {"ok": True, "id": sid}


@router.post("/cases")
def create_case(body: CaseIn, user=Depends(_get_user)):
    eng = _get_engine()
    if eng is None:
        raise HTTPException(503, "db unavailable")
    cid = "ec_" + secrets.token_hex(4)
    from sqlalchemy import text
    with eng.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO dash.dash_eval_cases
                  (id, suite_id, name, input_prompt, expected_output,
                   expected_tool_calls, judge_prompt, max_latency_ms)
                VALUES (:id, :sid, :nm, :ip, :eo,
                        CAST(:etc AS jsonb), :jp, :mlat)
                """
            ),
            {"id": cid, "sid": body.suite_id, "nm": body.name,
             "ip": body.input_prompt, "eo": body.expected_output,
             "etc": _json.dumps(body.expected_tool_calls),
             "jp": body.judge_prompt, "mlat": body.max_latency_ms},
        )
    return {"ok": True, "id": cid}


@router.get("/suites/{sid}/cases")
def list_cases(sid: str, user=Depends(_get_user)):
    eng = _get_engine()
    if eng is None:
        return {"cases": []}
    from sqlalchemy import text
    with eng.connect() as conn:
        rows = conn.execute(
            text("SELECT * FROM dash.dash_eval_cases WHERE suite_id=:s"),
            {"s": sid},
        ).mappings().all()
    return {"cases": [dict(r) for r in rows]}


@router.post("/suites/{sid}/run")
async def run_suite(sid: str, user=Depends(_get_user)):
    from dash.evals.runner import run_suite as _run
    import asyncio
    result = await asyncio.to_thread(_run, sid, user.get("id") if user else None)
    return result


@router.get("/runs")
def list_runs(
    suite_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    user=Depends(_get_user),
):
    eng = _get_engine()
    if eng is None:
        return {"runs": []}
    from sqlalchemy import text
    with eng.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT id, suite_id, status, total_cases, passed, failed,
                       pass_rate, avg_latency_ms, started_at, finished_at, notes
                FROM dash.dash_eval_runs
                WHERE (:s IS NULL OR suite_id = :s)
                ORDER BY started_at DESC LIMIT :lim
                """
            ),
            {"s": suite_id, "lim": limit},
        ).mappings().all()
    return {"runs": [dict(r) for r in rows]}


@router.get("/runs/{rid}")
def get_run(rid: str, user=Depends(_get_user)):
    eng = _get_engine()
    if eng is None:
        raise HTTPException(503, "db unavailable")
    from sqlalchemy import text
    with eng.connect() as conn:
        run = conn.execute(
            text("SELECT * FROM dash.dash_eval_runs WHERE id=:id"),
            {"id": rid},
        ).mappings().first()
        if not run:
            raise HTTPException(404, "run_not_found")
        results = conn.execute(
            text(
                "SELECT case_id, case_name, status, score, judge_reason, "
                "       latency_ms, error FROM dash.dash_eval_results "
                "WHERE run_id=:id ORDER BY created_at"
            ),
            {"id": rid},
        ).mappings().all()
    return {"run": dict(run), "results": [dict(r) for r in results]}


@router.post("/suites/{sid}/baseline")
def set_baseline_endpoint(
    sid: str, source_run_id: Optional[str] = Query(None),
    notes: Optional[str] = Query(None), user=Depends(_get_user),
):
    from dash.evals.runner import set_baseline
    return set_baseline(sid, source_run_id, notes, user.get("id") if user else None)


@router.get("/suites/{sid}/baseline")
def get_baseline(sid: str, user=Depends(_get_user)):
    eng = _get_engine()
    if eng is None:
        return {"baseline": None}
    from sqlalchemy import text
    with eng.connect() as conn:
        row = conn.execute(
            text(
                "SELECT pass_rate, avg_latency_ms, set_at, source_run_id, notes "
                "FROM dash.dash_eval_baselines WHERE suite_id=:s "
                "ORDER BY set_at DESC LIMIT 1"
            ),
            {"s": sid},
        ).mappings().first()
    return {"baseline": dict(row) if row else None}


class SqlPairGradeIn(BaseModel):
    input_prompt: str
    expected_sql: str
    expected_dialect: Optional[str] = "postgres"
    generated_sql: Optional[str] = None
    case_id: Optional[str] = None
    case_name: Optional[str] = None


@router.post("/grade-sql-pair")
async def grade_sql_pair_endpoint(body: SqlPairGradeIn, user=Depends(_get_user)):
    """Ad-hoc SQL pair grader — exec both SQLs + compare frames + LLM judge.

    Returns the full graded record (status, score, reason, compare, judge).
    """
    import asyncio
    eng = _get_engine()
    if eng is None:
        raise HTTPException(503, "db unavailable")
    case_row = {
        "id": body.case_id or "ad_hoc",
        "name": body.case_name or "ad_hoc",
        "input_prompt": body.input_prompt,
        "expected_sql": body.expected_sql,
        "expected_dialect": body.expected_dialect or "postgres",
        "generated_sql_hint": body.generated_sql or "",
    }
    try:
        from dash.evals.sql_result_grader import grade_case
    except Exception as e:
        raise HTTPException(500, f"grader import failed: {e}")
    try:
        result = await asyncio.to_thread(grade_case, case_row, eng)
        return {"ok": True, "result": result}
    except Exception as e:
        logger.exception("grade-sql-pair failed")
        raise HTTPException(500, f"grade error: {e}")


@router.get("/secret-leaks")
def list_leaks(
    days: int = Query(14, ge=1, le=90),
    limit: int = Query(100, ge=1, le=1000),
    user=Depends(_get_user),
):
    if not user or not user.get("is_super_admin"):
        raise HTTPException(403, "super_admin_required")
    eng = _get_engine()
    if eng is None:
        return {"leaks": []}
    from sqlalchemy import text
    with eng.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT id, agent_name, project_slug, pattern_matched,
                       match_excerpt, action, created_at
                FROM dash.dash_secret_leaks
                WHERE created_at > now() - (:d || ' days')::interval
                ORDER BY created_at DESC LIMIT :lim
                """
            ),
            {"d": days, "lim": limit},
        ).mappings().all()
    return {"leaks": [dict(r) for r in rows]}
