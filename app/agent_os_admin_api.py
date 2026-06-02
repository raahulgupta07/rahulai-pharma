"""Agent-OS admin backend (super-admin only). Migration 121.

Reuses existing user_agents / dash_autonomous_workflows / dash_traces tables.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import text

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/agent-os-admin", tags=["agent-os-admin"])


def _get_user(request: Request) -> dict:
    user = getattr(getattr(request, "state", None), "user", None)
    if not user:
        raise HTTPException(401, "Not authenticated")
    return user


def _require_super(user: dict):
    if not user.get("is_super_admin") and not user.get("is_super"):
        raise HTTPException(403, "super-admin only")


def _gate(request: Request) -> dict:
    user = _get_user(request)
    _require_super(user)
    return user


def _engine():
    from db.session import get_sql_engine
    return get_sql_engine()


def _wengine():
    from db.session import get_write_engine
    return get_write_engine()


def _rows(eng, sql: str, params: dict | None = None) -> list[dict]:
    try:
        with eng.connect() as c:
            res = c.execute(text(sql), params or {}).fetchall()
            return [dict(r._mapping) for r in res]
    except Exception as e:
        logger.warning(f"_rows failed for sql={sql[:80]!r}: {e}")
        return []


def _scalar(eng, sql: str, params: dict | None = None, default=None):
    try:
        with eng.connect() as c:
            r = c.execute(text(sql), params or {}).fetchone()
            return r[0] if r else default
    except Exception:
        return default


def _has_table(eng, schema: str, table: str) -> bool:
    try:
        with eng.connect() as c:
            r = c.execute(
                text(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema=:s AND table_name=:t LIMIT 1"
                ),
                {"s": schema, "t": table},
            ).fetchone()
            return r is not None
    except Exception:
        return False


# /registry — derive from user_agents + traces
@router.get("/registry")
def registry(request: Request):
    _gate(request)
    eng = _engine()

    out: list[dict] = []

    # user_agents lives in public schema (per migration 037)
    if _has_table(eng, "public", "user_agents"):
        try:
            with eng.connect() as c:
                rows = c.execute(
                    text(
                        "SELECT id::text AS agent_id, "
                        "       persona_json, state, enabled, "
                        "       created_at "
                        "FROM public.user_agents "
                        "ORDER BY created_at DESC LIMIT 500"
                    )
                ).fetchall()
        except Exception as e:
            logger.warning(f"/registry user_agents query failed: {e}")
            rows = []

        # Try LEFT JOIN traces for runs_24h + p95_ms; fall back gracefully.
        trace_stats: dict[str, dict] = {}
        if _has_table(eng, "public", "dash_traces"):
            try:
                with eng.connect() as c:
                    tstats = c.execute(
                        text(
                            "SELECT meta->>'agent_id' AS agent_id, "
                            "       count(*) AS runs_24h, "
                            "       percentile_cont(0.95) WITHIN GROUP "
                            "         (ORDER BY duration_ms) AS p95_ms "
                            "FROM public.dash_traces "
                            "WHERE started_at > now() - interval '24 hours' "
                            "  AND meta ? 'agent_id' "
                            "GROUP BY meta->>'agent_id'"
                        )
                    ).fetchall()
                    for t in tstats:
                        m = dict(t._mapping)
                        aid = m.get("agent_id")
                        if aid:
                            trace_stats[aid] = {
                                "runs_24h": int(m.get("runs_24h") or 0),
                                "p95_ms": int(m.get("p95_ms") or 0),
                            }
            except Exception as e:
                logger.debug(f"/registry trace stats skipped: {e}")

        for r in rows:
            d = dict(r._mapping)
            aid = d.get("agent_id")
            persona = d.get("persona_json") or {}
            if isinstance(persona, str):
                try:
                    import json
                    persona = json.loads(persona)
                except Exception:
                    persona = {}
            stats = trace_stats.get(aid, {})
            out.append({
                "agent_id": aid,
                "role": (persona or {}).get("role") if isinstance(persona, dict) else None,
                "team": (persona or {}).get("team") if isinstance(persona, dict) else None,
                "runs_24h": stats.get("runs_24h", 0),
                "p95_ms": stats.get("p95_ms", 0),
                "state": d.get("state") or ("enabled" if d.get("enabled") else "disabled"),
            })

    return {"agents": out, "count": len(out)}


# /capabilities
@router.get("/capabilities")
def list_capabilities(request: Request):
    _gate(request)
    rows = _rows(
        _engine(),
        "SELECT id, name, gated, default_on, description, updated_at "
        "FROM dash.aos_capabilities ORDER BY name",
    )
    return {"capabilities": rows}


@router.patch("/capabilities/{cap_id}")
async def patch_capability(cap_id: int, request: Request):
    _gate(request)
    body = await request.json()
    sets: list[str] = []
    params: dict[str, Any] = {"i": cap_id}
    if "gated" in body:
        sets.append("gated = :g")
        params["g"] = bool(body["gated"])
    if "default_on" in body:
        sets.append("default_on = :d")
        params["d"] = bool(body["default_on"])
    if "description" in body:
        sets.append("description = :desc")
        params["desc"] = str(body["description"])[:1000]
    if not sets:
        raise HTTPException(400, "no fields to update")
    sets.append("updated_at = now()")
    with _wengine().begin() as c:
        c.execute(
            text(f"UPDATE dash.aos_capabilities SET {', '.join(sets)} WHERE id = :i"),
            params,
        )
    return {"ok": True}


# /quotas
@router.get("/quotas")
def list_quotas(request: Request):
    _gate(request)
    rows = _rows(
        _engine(),
        "SELECT id, agent_id, tokens_limit, calls_per_min, dollars_per_day, "
        "       tokens_used, dollars_used, window_resets_at, updated_at "
        "FROM dash.aos_quotas ORDER BY agent_id",
    )
    return {"quotas": rows}


@router.put("/quotas/{agent_id}")
async def upsert_quota(agent_id: str, request: Request):
    _gate(request)
    body = await request.json()
    params = {
        "a": agent_id,
        "tl": body.get("tokens_limit"),
        "cpm": body.get("calls_per_min"),
        "dpd": body.get("dollars_per_day"),
    }
    with _wengine().begin() as c:
        c.execute(
            text(
                "INSERT INTO dash.aos_quotas "
                "  (agent_id, tokens_limit, calls_per_min, dollars_per_day, updated_at) "
                "VALUES (:a, :tl, :cpm, :dpd, now()) "
                "ON CONFLICT (agent_id) DO UPDATE SET "
                "  tokens_limit = EXCLUDED.tokens_limit, "
                "  calls_per_min = EXCLUDED.calls_per_min, "
                "  dollars_per_day = EXCLUDED.dollars_per_day, "
                "  updated_at = now()"
            ),
            params,
        )
    return {"ok": True, "agent_id": agent_id}


# /models
@router.get("/models")
def list_models(request: Request):
    _gate(request)
    rows = _rows(
        _engine(),
        "SELECT id, name, role, p95_ms, cost_per_m_in, cost_per_m_out, enabled, updated_at "
        "FROM dash.aos_models ORDER BY name",
    )
    return {"models": rows}


@router.patch("/models/{model_id}")
async def patch_model(model_id: int, request: Request):
    _gate(request)
    body = await request.json()
    sets: list[str] = []
    params: dict[str, Any] = {"i": model_id}
    if "enabled" in body:
        sets.append("enabled = :e")
        params["e"] = bool(body["enabled"])
    if "cost_per_m_in" in body:
        sets.append("cost_per_m_in = :ci")
        params["ci"] = body["cost_per_m_in"]
    if "cost_per_m_out" in body:
        sets.append("cost_per_m_out = :co")
        params["co"] = body["cost_per_m_out"]
    if "p95_ms" in body:
        sets.append("p95_ms = :p")
        params["p"] = int(body["p95_ms"])
    if "role" in body:
        sets.append("role = :r")
        params["r"] = str(body["role"])[:50]
    if not sets:
        raise HTTPException(400, "no fields to update")
    sets.append("updated_at = now()")
    with _wengine().begin() as c:
        c.execute(
            text(f"UPDATE dash.aos_models SET {', '.join(sets)} WHERE id = :i"),
            params,
        )
    return {"ok": True}


# /tools
@router.get("/tools")
def list_tools(request: Request):
    _gate(request)
    rows = _rows(
        _engine(),
        "SELECT id, tool_name, owner, enabled, calls_24h, err_pct, avg_ms, updated_at "
        "FROM dash.aos_tool_registry ORDER BY tool_name",
    )
    return {"tools": rows}


@router.patch("/tools/{tool_id}")
async def patch_tool(tool_id: int, request: Request):
    _gate(request)
    body = await request.json()
    sets: list[str] = []
    params: dict[str, Any] = {"i": tool_id}
    if "enabled" in body:
        sets.append("enabled = :e")
        params["e"] = bool(body["enabled"])
    if "owner" in body:
        sets.append("owner = :o")
        params["o"] = str(body["owner"])[:100]
    if not sets:
        raise HTTPException(400, "no fields to update")
    sets.append("updated_at = now()")
    with _wengine().begin() as c:
        c.execute(
            text(f"UPDATE dash.aos_tool_registry SET {', '.join(sets)} WHERE id = :i"),
            params,
        )
    return {"ok": True}


# /memory — stats from pgvector tables (best-effort)
@router.get("/memory")
def memory_stats(request: Request):
    _gate(request)
    eng = _engine()
    out: list[dict] = []

    # Probe common vector stores via pg_class for fast row-count fallback.
    candidates = [
        ("dash_vectors", "pgvector"),
        ("dash_knowledge_triples", "kg"),
        ("dash_memories", "memory"),
        ("agent_memory_events", "agent_mem"),
        ("dash_company_brain", "brain"),
    ]
    for tbl, backend in candidates:
        # Try both dash + public schemas.
        found = False
        for sch in ("dash", "public"):
            if not _has_table(eng, sch, tbl):
                continue
            found = True
            est = _scalar(
                eng,
                "SELECT reltuples::bigint FROM pg_class c "
                "JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname=:s AND c.relname=:t",
                {"s": sch, "t": tbl},
                default=0,
            )
            size_mb = _scalar(
                eng,
                "SELECT pg_total_relation_size(format('%I.%I', :s, :t)::regclass) / 1024 / 1024",
                {"s": sch, "t": tbl},
                default=0,
            )
            out.append({
                "store": f"{sch}.{tbl}",
                "vectors": int(est or 0) if "vector" in tbl else 0,
                "docs": int(est or 0),
                "size_mb": int(size_mb or 0),
                "backend": backend,
            })
            break
        if not found:
            # Skip silently — tolerant fallback.
            continue

    return {"memory": out}


# /workflows — reuse dash_autonomous_workflows + orphan check
@router.get("/workflows")
def list_workflows(request: Request):
    _gate(request)
    eng = _engine()
    rows: list[dict] = []
    try:
        with eng.connect() as c:
            res = c.execute(
                text(
                    "SELECT w.id, w.project_slug, w.name, w.description, "
                    "       w.schedule, w.schedule_cron, w.status, "
                    "       w.last_run_at, w.last_error, w.created_at, "
                    "       (p.slug IS NULL) AS orphan "
                    "FROM dash.dash_autonomous_workflows w "
                    "LEFT JOIN public.dash_projects p ON p.slug = w.project_slug "
                    "ORDER BY w.created_at DESC LIMIT 500"
                )
            ).fetchall()
            rows = [dict(r._mapping) for r in res]
    except Exception as e:
        logger.warning(f"/workflows query failed: {e}")
        # Fall back to no-join version.
        rows = _rows(
            eng,
            "SELECT id, project_slug, name, description, schedule, "
            "       status, last_run_at, last_error, created_at "
            "FROM dash.dash_autonomous_workflows ORDER BY created_at DESC LIMIT 500",
        )
        for r in rows:
            r["orphan"] = None

    orphan_count = sum(1 for r in rows if r.get("orphan"))
    return {"workflows": rows, "count": len(rows), "orphan_count": orphan_count}


# /kill-switch
@router.get("/kill-switch")
def get_kill_switch(request: Request):
    _gate(request)
    rows = _rows(
        _engine(),
        "SELECT id, armed, last_changed_at, last_changed_by "
        "FROM dash.aos_kill_switch WHERE id = 1",
    )
    if not rows:
        # Ensure row exists.
        with _wengine().begin() as c:
            c.execute(
                text(
                    "INSERT INTO dash.aos_kill_switch (id, armed) VALUES (1, true) "
                    "ON CONFLICT (id) DO NOTHING"
                )
            )
        return {"armed": True, "last_changed_at": None, "last_changed_by": None}
    return rows[0]


@router.post("/kill-switch/toggle")
async def toggle_kill_switch(request: Request):
    user = _gate(request)
    body = await request.json()
    if "armed" not in body:
        raise HTTPException(400, "armed:bool required")
    armed = bool(body["armed"])
    who = (user.get("username") or user.get("user_id") or "unknown")
    with _wengine().begin() as c:
        c.execute(
            text(
                "INSERT INTO dash.aos_kill_switch (id, armed, last_changed_at, last_changed_by) "
                "VALUES (1, :a, now(), :w) "
                "ON CONFLICT (id) DO UPDATE SET "
                "  armed = EXCLUDED.armed, "
                "  last_changed_at = now(), "
                "  last_changed_by = EXCLUDED.last_changed_by"
            ),
            {"a": armed, "w": str(who)[:100]},
        )
    logger.info(f"kill_switch toggled armed={armed} by={who}")
    return {"ok": True, "armed": armed, "by": str(who)}


# /overrides
@router.get("/overrides")
def list_overrides(request: Request):
    _gate(request)
    rows = _rows(
        _engine(),
        "SELECT id, agent_id, state, detail, set_at, set_by "
        "FROM dash.aos_agent_overrides ORDER BY set_at DESC LIMIT 500",
    )
    return {"overrides": rows}


@router.post("/overrides")
async def create_override(request: Request):
    user = _gate(request)
    body = await request.json()
    agent_id = (body.get("agent_id") or "").strip()
    state = (body.get("state") or "").strip()
    if not agent_id or state not in ("paused", "rate-limited"):
        raise HTTPException(400, "agent_id + state ∈ {paused,rate-limited} required")
    who = user.get("username") or user.get("user_id") or "unknown"
    with _wengine().begin() as c:
        row = c.execute(
            text(
                "INSERT INTO dash.aos_agent_overrides (agent_id, state, detail, set_by) "
                "VALUES (:a, :s, :d, :w) RETURNING id"
            ),
            {
                "a": agent_id,
                "s": state,
                "d": str(body.get("detail") or "")[:500],
                "w": str(who)[:100],
            },
        ).fetchone()
    return {"ok": True, "id": row[0] if row else None}


@router.delete("/overrides/{override_id}")
def delete_override(override_id: int, request: Request):
    _gate(request)
    with _wengine().begin() as c:
        res = c.execute(
            text("DELETE FROM dash.aos_agent_overrides WHERE id = :i"),
            {"i": override_id},
        )
    return {"ok": True, "deleted": res.rowcount}


# /cost-guard
@router.get("/cost-guard")
def get_cost_guard(request: Request):
    _gate(request)
    rows = _rows(
        _engine(),
        "SELECT id, daily_budget, used_today, hard_stop_pct, alert_pct, "
        "       alert_email, updated_at "
        "FROM dash.aos_cost_guard WHERE id = 1",
    )
    if not rows:
        with _wengine().begin() as c:
            c.execute(
                text("INSERT INTO dash.aos_cost_guard (id) VALUES (1) ON CONFLICT (id) DO NOTHING")
            )
        return {
            "id": 1, "daily_budget": 200, "used_today": 0,
            "hard_stop_pct": 90, "alert_pct": 75, "alert_email": None,
        }
    return rows[0]


@router.patch("/cost-guard")
async def patch_cost_guard(request: Request):
    _gate(request)
    body = await request.json()
    sets: list[str] = []
    params: dict[str, Any] = {}
    if "daily_budget" in body:
        sets.append("daily_budget = :db")
        params["db"] = body["daily_budget"]
    if "hard_stop_pct" in body:
        sets.append("hard_stop_pct = :hs")
        params["hs"] = int(body["hard_stop_pct"])
    if "alert_pct" in body:
        sets.append("alert_pct = :ap")
        params["ap"] = int(body["alert_pct"])
    if "alert_email" in body:
        sets.append("alert_email = :ae")
        params["ae"] = str(body["alert_email"])[:200] if body["alert_email"] else None
    if not sets:
        raise HTTPException(400, "no fields to update")
    sets.append("updated_at = now()")
    with _wengine().begin() as c:
        c.execute(
            text(f"UPDATE dash.aos_cost_guard SET {', '.join(sets)} WHERE id = 1"),
            params,
        )
    return {"ok": True}


# /summary — fleet snapshot
@router.get("/summary")
def summary(request: Request):
    _gate(request)
    eng = _engine()

    fleet_count = 0
    idle = 0
    degraded = 0
    if _has_table(eng, "public", "user_agents"):
        fleet_count = int(_scalar(eng, "SELECT count(*) FROM public.user_agents", default=0) or 0)
        idle = int(_scalar(
            eng,
            "SELECT count(*) FROM public.user_agents WHERE state IN ('building','archived') OR enabled = false",
            default=0,
        ) or 0)
        degraded = int(_scalar(
            eng,
            "SELECT count(*) FROM public.user_agents WHERE state = 'error'",
            default=0,
        ) or 0)

    ks = _rows(eng, "SELECT armed FROM dash.aos_kill_switch WHERE id = 1")
    armed = bool(ks[0]["armed"]) if ks else True

    cg = _rows(
        eng,
        "SELECT daily_budget, used_today FROM dash.aos_cost_guard WHERE id = 1",
    )
    budget_pct = 0.0
    if cg:
        db = float(cg[0]["daily_budget"] or 0)
        ut = float(cg[0]["used_today"] or 0)
        budget_pct = round(100.0 * ut / db, 1) if db > 0 else 0.0

    return {
        "fleet_count": fleet_count,
        "idle": idle,
        "degraded": degraded,
        "kill_switch_armed": armed,
        "budget_used_pct": budget_pct,
    }
