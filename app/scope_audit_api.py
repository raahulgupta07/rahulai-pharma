"""Chat Scope Audit API — Batch 3 sub-task D.

Per-chat-session view of what data/skills/tools/cost/errors a session touched.
Reads from `public.dash_traces` (span table), `public.dash_llm_costs` (cost
ledger), `public.dash_chat_sessions` (session metadata), and `ai.agno_sessions`
(runs JSONB) for tool/skill inference.

Fail-soft: if trace tables missing or empty → returns graceful empty arrays +
warning banner. Never raises HTTP 5xx.

All reads go through `db.session.get_sql_engine()` (cached shared engine,
read-only public schema guard).
"""

from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import text

logger = logging.getLogger("dash.scope_audit")

router = APIRouter(prefix="/api/scope-audit", tags=["scope-audit"])


# ── Pydantic response models ──────────────────────────────────────────────────
class SessionSummary(BaseModel):
    session_id: str
    user_id: int | None = None
    username: str | None = None
    project_slug: str | None = None
    first_message: str | None = None
    msg_count: int = 0
    created_at: str | None = None
    updated_at: str | None = None
    total_cost_usd: float = 0.0
    error_count: int = 0


class SpanRow(BaseModel):
    id: int
    trace_id: str
    parent_id: str | None = None
    name: str
    kind: str
    status: str
    duration_ms: int | None = None
    cost_usd: float | None = None
    tokens: int | None = None
    error: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    meta: dict | None = None


class SessionAuditResponse(BaseModel):
    session: SessionSummary
    timeline: list[SpanRow] = []
    tables_touched: list[dict] = []  # [{table, count}]
    skills_used: list[dict] = []     # [{skill_id, count, success_count}]
    tools_called: list[dict] = []    # [{tool, count, error_count}]
    rls_policies_fired: list[dict] = []  # [{policy, count}]
    errors: list[dict] = []          # [{span_name, error, started_at}]
    total_cost_usd: float = 0.0
    total_tokens: int = 0
    warning: str | None = None


class SummaryResponse(BaseModel):
    project_slug: str
    days: int
    session_count: int = 0
    total_cost_usd: float = 0.0
    error_rate: float = 0.0
    top_tables: list[dict] = []      # [{table, count}]
    top_skills: list[dict] = []      # [{skill_id, count}]
    top_users: list[dict] = []       # [{user_id, username, session_count, cost_usd}]
    top_tools: list[dict] = []
    warning: str | None = None


class UserAuditResponse(BaseModel):
    user_id: int
    username: str | None = None
    days: int
    session_count: int = 0
    project_count: int = 0
    total_cost_usd: float = 0.0
    recent_sessions: list[SessionSummary] = []
    top_projects: list[dict] = []     # [{project_slug, session_count, cost_usd}]
    top_tools: list[dict] = []
    warning: str | None = None


# ── Helpers ───────────────────────────────────────────────────────────────────
def _engine():
    """Return cached read-only SQL engine. Fail-soft if missing."""
    try:
        from db.session import get_sql_engine
        return get_sql_engine()
    except Exception as e:
        logger.warning(f"scope_audit: failed to get sql engine: {e}")
        return None


def _table_exists(conn, schema: str, table: str) -> bool:
    try:
        r = conn.execute(text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = :s AND table_name = :t"
        ), {"s": schema, "t": table}).fetchone()
        return r is not None
    except Exception:
        return False


_TABLE_RE = re.compile(
    r"\b(?:FROM|JOIN|UPDATE|INTO)\s+([a-zA-Z_][\w]*(?:\.[a-zA-Z_][\w]*)?)",
    re.IGNORECASE,
)


def _extract_tables_from_sql(sql: str) -> list[str]:
    """Naive table extraction from a SQL string. Returns lowercased FQNs."""
    if not sql or not isinstance(sql, str):
        return []
    found = []
    for m in _TABLE_RE.finditer(sql):
        t = m.group(1).strip().lower()
        # skip CTE aliases / aggregates  (single-word, common aliases)
        if t in {"select", "where", "group", "order", "limit", "values"}:
            continue
        found.append(t)
    return found


def _extract_from_meta(meta: Any, key_paths: list[list[str]]) -> Any:
    """Walk meta JSONB looking for first non-empty value at any path."""
    if not isinstance(meta, dict):
        return None
    for path in key_paths:
        cur = meta
        ok = True
        for k in path:
            if isinstance(cur, dict) and k in cur:
                cur = cur[k]
            else:
                ok = False
                break
        if ok and cur:
            return cur
    return None


def _safe_iso(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.isoformat()
    return str(v)


# ── Endpoint 1: GET /sessions ─────────────────────────────────────────────────
@router.get("/sessions")
def list_sessions(
    request: Request,
    project_slug: str = Query(..., description="Project slug to filter on"),
    limit: int = Query(50, ge=1, le=500),
):
    eng = _engine()
    if eng is None:
        return {"sessions": [], "warning": "SQL engine unavailable"}

    warning: str | None = None
    sessions: list[dict] = []
    try:
        with eng.connect() as conn:
            if not _table_exists(conn, "public", "dash_chat_sessions"):
                return {"sessions": [], "warning": "dash_chat_sessions table missing"}

            has_traces = _table_exists(conn, "public", "dash_traces")
            has_costs = _table_exists(conn, "public", "dash_llm_costs")
            has_agno = _table_exists(conn, "ai", "agno_sessions")

            if not has_traces:
                warning = "dash_traces table missing — counts may be partial"

            rows = conn.execute(text("""
                SELECT s.session_id, s.user_id, u.username, s.project_slug,
                       s.first_message, s.created_at, s.updated_at
                FROM public.dash_chat_sessions s
                LEFT JOIN public.dash_users u ON u.id = s.user_id
                WHERE s.project_slug = :slug
                ORDER BY s.updated_at DESC NULLS LAST, s.created_at DESC
                LIMIT :lim
            """), {"slug": project_slug, "lim": limit}).fetchall()

            for r in rows:
                sid = r[0]
                # message count (from agno runs if available)
                msg_count = 0
                if has_agno:
                    try:
                        mr = conn.execute(text(
                            "SELECT jsonb_array_length(runs) "
                            "FROM ai.agno_sessions WHERE session_id = :sid"
                        ), {"sid": sid}).fetchone()
                        msg_count = int(mr[0]) if mr and mr[0] is not None else 0
                    except Exception:
                        msg_count = 0

                # total cost + error count from traces (cheap aggregates)
                total_cost = 0.0
                err_count = 0
                if has_traces:
                    try:
                        agg = conn.execute(text("""
                            SELECT
                                COALESCE(SUM(cost_usd), 0)::float,
                                COUNT(*) FILTER (WHERE status = 'error')::int
                            FROM public.dash_traces
                            WHERE meta->>'session_id' = :sid OR trace_id = :sid
                        """), {"sid": sid}).fetchone()
                        if agg:
                            total_cost = float(agg[0] or 0.0)
                            err_count = int(agg[1] or 0)
                    except Exception:
                        pass

                sessions.append({
                    "session_id": sid,
                    "user_id": r[1],
                    "username": r[2],
                    "project_slug": r[3],
                    "first_message": (r[4] or "")[:200] if r[4] else None,
                    "msg_count": msg_count,
                    "created_at": _safe_iso(r[5]),
                    "updated_at": _safe_iso(r[6]),
                    "total_cost_usd": total_cost,
                    "error_count": err_count,
                })
    except Exception as e:
        logger.exception(f"scope_audit list_sessions failed: {e}")
        return {"sessions": [], "warning": f"query failed: {str(e)[:200]}"}

    return {"sessions": sessions, "warning": warning}


# ── Endpoint 2: GET /session/{session_id} ─────────────────────────────────────
@router.get("/session/{session_id}")
def session_audit(session_id: str, request: Request):
    eng = _engine()
    if eng is None:
        return _empty_session_audit(session_id, "SQL engine unavailable")

    warning: str | None = None
    try:
        with eng.connect() as conn:
            # Resolve session metadata
            sess_meta = None
            if _table_exists(conn, "public", "dash_chat_sessions"):
                r = conn.execute(text("""
                    SELECT s.session_id, s.user_id, u.username, s.project_slug,
                           s.first_message, s.created_at, s.updated_at
                    FROM public.dash_chat_sessions s
                    LEFT JOIN public.dash_users u ON u.id = s.user_id
                    WHERE s.session_id = :sid
                """), {"sid": session_id}).fetchone()
                if r:
                    sess_meta = {
                        "session_id": r[0],
                        "user_id": r[1],
                        "username": r[2],
                        "project_slug": r[3],
                        "first_message": (r[4] or "")[:200] if r[4] else None,
                        "created_at": _safe_iso(r[5]),
                        "updated_at": _safe_iso(r[6]),
                    }
            if sess_meta is None:
                sess_meta = {"session_id": session_id}

            has_traces = _table_exists(conn, "public", "dash_traces")
            has_agno = _table_exists(conn, "ai", "agno_sessions")
            has_skill_invocations = _table_exists(conn, "public", "dash_skill_invocations") or _table_exists(conn, "dash", "dash_skill_invocations")

            # ── Timeline (spans) ──
            timeline: list[dict] = []
            tables_touched: Counter = Counter()
            skills_used: Counter = Counter()
            tools_called: Counter = Counter()
            tool_errors: Counter = Counter()
            rls_policies: Counter = Counter()
            errors: list[dict] = []
            total_cost = 0.0
            total_tokens = 0

            if has_traces:
                try:
                    span_rows = conn.execute(text("""
                        SELECT id, trace_id, parent_id, name, kind, status,
                               duration_ms, cost_usd, tokens, error,
                               started_at, finished_at, meta
                        FROM public.dash_traces
                        WHERE meta->>'session_id' = :sid OR trace_id = :sid
                        ORDER BY started_at ASC
                        LIMIT 500
                    """), {"sid": session_id}).fetchall()

                    for sr in span_rows:
                        meta = sr[12] if isinstance(sr[12], dict) else {}
                        name = sr[3] or ""
                        kind = sr[4] or ""
                        status = sr[5] or ""

                        timeline.append({
                            "id": sr[0],
                            "trace_id": sr[1],
                            "parent_id": sr[2],
                            "name": name,
                            "kind": kind,
                            "status": status,
                            "duration_ms": sr[6],
                            "cost_usd": float(sr[7]) if sr[7] is not None else None,
                            "tokens": sr[8],
                            "error": sr[9],
                            "started_at": _safe_iso(sr[10]),
                            "finished_at": _safe_iso(sr[11]),
                            "meta": meta,
                        })

                        if sr[7] is not None:
                            total_cost += float(sr[7] or 0)
                        if sr[8] is not None:
                            total_tokens += int(sr[8] or 0)
                        if status == "error":
                            errors.append({
                                "span_name": name,
                                "error": sr[9],
                                "started_at": _safe_iso(sr[10]),
                            })

                        # tool detection: kind == 'tool' OR name like chat.X.tool
                        tool_name = _extract_from_meta(meta, [
                            ["tool"], ["tool_name"], ["fn"]
                        ])
                        if not tool_name and "." in name:
                            # e.g. chat.analyst.run_sql_query  → run_sql_query
                            parts = name.split(".")
                            if len(parts) >= 3:
                                tool_name = parts[-1]
                        if tool_name:
                            tools_called[str(tool_name)] += 1
                            if status == "error":
                                tool_errors[str(tool_name)] += 1

                        # skill detection: skill_id in meta
                        skill = _extract_from_meta(meta, [
                            ["skill_id"], ["skill"]
                        ])
                        if skill:
                            skills_used[str(skill)] += 1

                        # tables touched: parse SQL in meta.sql / meta.query
                        sql_str = _extract_from_meta(meta, [
                            ["sql"], ["query"], ["statement"]
                        ])
                        for tbl in _extract_tables_from_sql(sql_str or ""):
                            tables_touched[tbl] += 1

                        # RLS: meta.rls_policy or kind == 'rls'
                        rls = _extract_from_meta(meta, [
                            ["rls_policy"], ["policy"]
                        ])
                        if rls:
                            rls_policies[str(rls)] += 1
                except Exception as e:
                    logger.warning(f"scope_audit: trace read failed for {session_id}: {e}")
                    warning = (warning or "") + f" trace read partial: {str(e)[:120]}"
            else:
                warning = "dash_traces missing — timeline empty (fallback to chat history only)"

            # ── Fallback: parse agno runs for tool/SQL calls ──
            if has_agno and (not tools_called or not tables_touched):
                try:
                    r = conn.execute(text(
                        "SELECT runs FROM ai.agno_sessions WHERE session_id = :sid"
                    ), {"sid": session_id}).fetchone()
                    runs = (r[0] if r and isinstance(r[0], list) else []) or []
                    for run in runs:
                        if not isinstance(run, dict):
                            continue
                        # tool calls
                        for tc in (run.get("tool_calls") or []) or []:
                            if isinstance(tc, dict):
                                nm = tc.get("name") or tc.get("function", {}).get("name")
                                if nm:
                                    tools_called[str(nm)] += 1
                                args = tc.get("args") or tc.get("arguments") or {}
                                if isinstance(args, dict):
                                    sql_str = args.get("sql") or args.get("query")
                                    for tbl in _extract_tables_from_sql(sql_str or ""):
                                        tables_touched[tbl] += 1
                except Exception as e:
                    logger.warning(f"scope_audit: agno fallback failed: {e}")

            # ── Skill success_count cross-ref ──
            skills_resp: list[dict] = []
            if skills_used and has_skill_invocations:
                try:
                    skill_ids = list(skills_used.keys())
                    sc = conn.execute(text(f"""
                        SELECT skill_id, COUNT(*) FILTER (WHERE status = 'success')
                        FROM public.dash_skill_invocations
                        WHERE skill_id = ANY(:ids) AND session_id = :sid
                        GROUP BY skill_id
                    """), {"ids": skill_ids, "sid": session_id}).fetchall()
                    success_map = {row[0]: int(row[1] or 0) for row in sc}
                    for sk, cnt in skills_used.most_common():
                        skills_resp.append({
                            "skill_id": sk,
                            "count": cnt,
                            "success_count": success_map.get(sk, 0),
                        })
                except Exception:
                    skills_resp = [
                        {"skill_id": sk, "count": cnt, "success_count": 0}
                        for sk, cnt in skills_used.most_common()
                    ]
            else:
                skills_resp = [
                    {"skill_id": sk, "count": cnt, "success_count": 0}
                    for sk, cnt in skills_used.most_common()
                ]

            sess_meta["msg_count"] = sum(1 for s in timeline if s["kind"] == "chat")
            sess_meta["total_cost_usd"] = total_cost
            sess_meta["error_count"] = len(errors)

            return {
                "session": sess_meta,
                "timeline": timeline,
                "tables_touched": [
                    {"table": t, "count": c} for t, c in tables_touched.most_common(50)
                ],
                "skills_used": skills_resp,
                "tools_called": [
                    {"tool": t, "count": c, "error_count": tool_errors.get(t, 0)}
                    for t, c in tools_called.most_common(50)
                ],
                "rls_policies_fired": [
                    {"policy": p, "count": c} for p, c in rls_policies.most_common(20)
                ],
                "errors": errors,
                "total_cost_usd": total_cost,
                "total_tokens": total_tokens,
                "warning": warning,
            }
    except Exception as e:
        logger.exception(f"scope_audit session_audit failed: {e}")
        return _empty_session_audit(session_id, f"query failed: {str(e)[:200]}")


def _empty_session_audit(session_id: str, warning: str) -> dict:
    return {
        "session": {"session_id": session_id},
        "timeline": [],
        "tables_touched": [],
        "skills_used": [],
        "tools_called": [],
        "rls_policies_fired": [],
        "errors": [],
        "total_cost_usd": 0.0,
        "total_tokens": 0,
        "warning": warning,
    }


# ── Endpoint 3: GET /summary ──────────────────────────────────────────────────
@router.get("/summary")
def project_summary(
    request: Request,
    project_slug: str = Query(..., description="Project slug"),
    days: int = Query(7, ge=1, le=365),
):
    eng = _engine()
    if eng is None:
        return _empty_summary(project_slug, days, "SQL engine unavailable")

    warning: str | None = None
    try:
        with eng.connect() as conn:
            has_sessions = _table_exists(conn, "public", "dash_chat_sessions")
            has_traces = _table_exists(conn, "public", "dash_traces")
            since = datetime.utcnow() - timedelta(days=days)

            # session count
            session_count = 0
            if has_sessions:
                r = conn.execute(text("""
                    SELECT COUNT(*) FROM public.dash_chat_sessions
                    WHERE project_slug = :slug AND created_at >= :since
                """), {"slug": project_slug, "since": since}).fetchone()
                session_count = int(r[0] or 0) if r else 0

            # cost + error rate from traces
            total_cost = 0.0
            error_rate = 0.0
            top_tables: list[dict] = []
            top_skills: list[dict] = []
            top_tools: list[dict] = []

            if has_traces:
                try:
                    agg = conn.execute(text("""
                        SELECT
                            COALESCE(SUM(cost_usd), 0)::float,
                            COUNT(*)::int,
                            COUNT(*) FILTER (WHERE status = 'error')::int
                        FROM public.dash_traces
                        WHERE project_slug = :slug AND started_at >= :since
                    """), {"slug": project_slug, "since": since}).fetchone()
                    if agg:
                        total_cost = float(agg[0] or 0.0)
                        total = int(agg[1] or 0)
                        errs = int(agg[2] or 0)
                        error_rate = (errs / total) if total > 0 else 0.0

                    # top skills via meta->>'skill_id'
                    sk_rows = conn.execute(text("""
                        SELECT meta->>'skill_id' AS skill_id, COUNT(*)::int
                        FROM public.dash_traces
                        WHERE project_slug = :slug AND started_at >= :since
                          AND meta ? 'skill_id'
                        GROUP BY meta->>'skill_id'
                        ORDER BY COUNT(*) DESC
                        LIMIT 10
                    """), {"slug": project_slug, "since": since}).fetchall()
                    top_skills = [{"skill_id": r[0], "count": r[1]} for r in sk_rows if r[0]]

                    # top tables via SQL parsing — sample last 200 spans w/ sql in meta
                    sql_rows = conn.execute(text("""
                        SELECT meta->>'sql' AS sql_str
                        FROM public.dash_traces
                        WHERE project_slug = :slug AND started_at >= :since
                          AND (meta ? 'sql' OR meta ? 'query')
                        LIMIT 500
                    """), {"slug": project_slug, "since": since}).fetchall()
                    tbl_counter: Counter = Counter()
                    for r in sql_rows:
                        for tbl in _extract_tables_from_sql(r[0] or ""):
                            tbl_counter[tbl] += 1
                    top_tables = [{"table": t, "count": c} for t, c in tbl_counter.most_common(10)]

                    # top tools via name parsing
                    tool_rows = conn.execute(text("""
                        SELECT name, COUNT(*)::int
                        FROM public.dash_traces
                        WHERE project_slug = :slug AND started_at >= :since
                          AND kind = 'tool'
                        GROUP BY name
                        ORDER BY COUNT(*) DESC
                        LIMIT 10
                    """), {"slug": project_slug, "since": since}).fetchall()
                    top_tools = [{"tool": r[0], "count": r[1]} for r in tool_rows]
                except Exception as e:
                    logger.warning(f"scope_audit summary trace agg failed: {e}")
                    warning = f"trace aggregates partial: {str(e)[:120]}"
            else:
                warning = "dash_traces missing — cost/error/skills empty"

            # top users by session count
            top_users: list[dict] = []
            if has_sessions:
                try:
                    ur = conn.execute(text("""
                        SELECT s.user_id, u.username, COUNT(*)::int AS sess_count
                        FROM public.dash_chat_sessions s
                        LEFT JOIN public.dash_users u ON u.id = s.user_id
                        WHERE s.project_slug = :slug AND s.created_at >= :since
                        GROUP BY s.user_id, u.username
                        ORDER BY sess_count DESC
                        LIMIT 10
                    """), {"slug": project_slug, "since": since}).fetchall()
                    top_users = [
                        {"user_id": r[0], "username": r[1], "session_count": r[2], "cost_usd": 0.0}
                        for r in ur
                    ]
                except Exception:
                    pass

            return {
                "project_slug": project_slug,
                "days": days,
                "session_count": session_count,
                "total_cost_usd": total_cost,
                "error_rate": error_rate,
                "top_tables": top_tables,
                "top_skills": top_skills,
                "top_users": top_users,
                "top_tools": top_tools,
                "warning": warning,
            }
    except Exception as e:
        logger.exception(f"scope_audit summary failed: {e}")
        return _empty_summary(project_slug, days, f"query failed: {str(e)[:200]}")


def _empty_summary(project_slug: str, days: int, warning: str) -> dict:
    return {
        "project_slug": project_slug,
        "days": days,
        "session_count": 0,
        "total_cost_usd": 0.0,
        "error_rate": 0.0,
        "top_tables": [],
        "top_skills": [],
        "top_users": [],
        "top_tools": [],
        "warning": warning,
    }


# ── Endpoint 4: GET /user/{user_id} ───────────────────────────────────────────
@router.get("/user/{user_id}")
def user_audit(
    user_id: int,
    request: Request,
    days: int = Query(30, ge=1, le=365),
):
    eng = _engine()
    if eng is None:
        return _empty_user_audit(user_id, days, "SQL engine unavailable")

    warning: str | None = None
    try:
        with eng.connect() as conn:
            has_sessions = _table_exists(conn, "public", "dash_chat_sessions")
            has_traces = _table_exists(conn, "public", "dash_traces")
            since = datetime.utcnow() - timedelta(days=days)

            # username
            username = None
            try:
                ur = conn.execute(text(
                    "SELECT username FROM public.dash_users WHERE id = :uid"
                ), {"uid": user_id}).fetchone()
                username = ur[0] if ur else None
            except Exception:
                pass

            if not has_sessions:
                return _empty_user_audit(user_id, days, "dash_chat_sessions missing")

            # recent sessions
            sess_rows = conn.execute(text("""
                SELECT s.session_id, s.project_slug, s.first_message,
                       s.created_at, s.updated_at
                FROM public.dash_chat_sessions s
                WHERE s.user_id = :uid AND s.created_at >= :since
                ORDER BY s.created_at DESC
                LIMIT 50
            """), {"uid": user_id, "since": since}).fetchall()

            recent_sessions = []
            session_ids = []
            for r in sess_rows:
                session_ids.append(r[0])
                recent_sessions.append({
                    "session_id": r[0],
                    "user_id": user_id,
                    "username": username,
                    "project_slug": r[1],
                    "first_message": (r[2] or "")[:200] if r[2] else None,
                    "created_at": _safe_iso(r[3]),
                    "updated_at": _safe_iso(r[4]),
                    "msg_count": 0,
                    "total_cost_usd": 0.0,
                    "error_count": 0,
                })

            # project rollup
            top_projects: list[dict] = []
            try:
                pr = conn.execute(text("""
                    SELECT project_slug, COUNT(*)::int
                    FROM public.dash_chat_sessions
                    WHERE user_id = :uid AND created_at >= :since
                    GROUP BY project_slug
                    ORDER BY COUNT(*) DESC
                    LIMIT 10
                """), {"uid": user_id, "since": since}).fetchall()
                top_projects = [
                    {"project_slug": r[0] or "(none)", "session_count": r[1], "cost_usd": 0.0}
                    for r in pr
                ]
            except Exception:
                pass

            # tools rollup + total cost from traces
            top_tools: list[dict] = []
            total_cost = 0.0
            if has_traces and session_ids:
                try:
                    # Cost across all this user's sessions in window
                    cr = conn.execute(text("""
                        SELECT COALESCE(SUM(cost_usd), 0)::float
                        FROM public.dash_traces
                        WHERE meta->>'session_id' = ANY(:sids) AND started_at >= :since
                    """), {"sids": session_ids, "since": since}).fetchone()
                    total_cost = float(cr[0] or 0.0) if cr else 0.0

                    tr = conn.execute(text("""
                        SELECT name, COUNT(*)::int
                        FROM public.dash_traces
                        WHERE meta->>'session_id' = ANY(:sids)
                          AND kind = 'tool' AND started_at >= :since
                        GROUP BY name
                        ORDER BY COUNT(*) DESC
                        LIMIT 10
                    """), {"sids": session_ids, "since": since}).fetchall()
                    top_tools = [{"tool": r[0], "count": r[1]} for r in tr]
                except Exception as e:
                    warning = f"trace partial: {str(e)[:120]}"
            elif not has_traces:
                warning = "dash_traces missing — cost/tools empty"

            return {
                "user_id": user_id,
                "username": username,
                "days": days,
                "session_count": len(recent_sessions),
                "project_count": len(top_projects),
                "total_cost_usd": total_cost,
                "recent_sessions": recent_sessions,
                "top_projects": top_projects,
                "top_tools": top_tools,
                "warning": warning,
            }
    except Exception as e:
        logger.exception(f"scope_audit user_audit failed: {e}")
        return _empty_user_audit(user_id, days, f"query failed: {str(e)[:200]}")


def _empty_user_audit(user_id: int, days: int, warning: str) -> dict:
    return {
        "user_id": user_id,
        "username": None,
        "days": days,
        "session_count": 0,
        "project_count": 0,
        "total_cost_usd": 0.0,
        "recent_sessions": [],
        "top_projects": [],
        "top_tools": [],
        "warning": warning,
    }
