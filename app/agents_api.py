"""
User-Agents API
===============

Per-user personal agent endpoints backed by MiroFish.

Tables (migration 037): dash.user_agents, dash.agent_memory_events, dash.agent_simulations, dash.agent_audit_log.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import create_engine as _sa_create_engine, text
from sqlalchemy.pool import NullPool

from app.user_agent_engine import get_engine as get_client
from db import db_url

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agents", tags=["agents"])
_engine = _sa_create_engine(db_url, poolclass=NullPool)

# Stable namespace for deriving UUIDs from integer dash_users.id values.
_UID_NS = uuid.UUID("6ba7b815-9dad-11d1-80b4-00c04fd430c8")


def _get_user(request: Request) -> dict:
    user = getattr(getattr(request, "state", None), "user", None)
    if not user:
        from app.auth import get_current_user
        user = get_current_user(request)
    if not user:
        raise HTTPException(401, "Not authenticated")
    return user


def _uid(user: dict) -> str:
    """Return UUID string for a user (deterministic from int user_id)."""
    raw = str(user.get("user_id") or user.get("id") or user.get("username") or "")
    if not raw:
        raise HTTPException(401, "Invalid user")
    try:
        return str(uuid.UUID(raw))
    except (ValueError, AttributeError):
        return str(uuid.uuid5(_UID_NS, raw))


def _audit(conn, agent_id: Optional[str], user_uuid: str, action: str, detail: dict | None = None):
    conn.execute(
        text("INSERT INTO dash.agent_audit_log (agent_id, user_id, action, detail) "
             "VALUES (:aid, :uid, :a, CAST(:d AS jsonb))"),
        {"aid": agent_id, "uid": user_uuid, "a": action, "d": json.dumps(detail or {})},
    )


def _row_to_dict(row) -> dict:
    return {
        "agent_id": str(row[0]),
        "user_id": str(row[1]),
        "persona": row[2] or {},
        "zep_session_id": row[3],
        "graph_id": row[4],
        "state": row[5],
        "enabled": row[6],
        "last_sync": row[7].isoformat() if row[7] else None,
        "version": row[8],
        "created_at": row[9].isoformat() if row[9] else None,
        "updated_at": row[10].isoformat() if row[10] else None,
    }


_AGENT_COLS = ("id, user_id, persona_json, zep_session_id, graph_id, state, "
               "enabled, last_sync, version, created_at, updated_at")


# ── 1. Bootstrap ──────────────────────────────────────────────────────────

@router.post("/bootstrap")
async def bootstrap_agent(request: Request):
    user = _get_user(request)
    uid = _uid(user)
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    signals = body.get("signals") or {"username": user.get("username")}

    client = get_client()
    try:
        persona_resp = await client.build_persona(uid, signals)
    except Exception as e:
        logger.exception("persona build failed")
        raise HTTPException(502, f"persona build failed: {e}")

    persona = persona_resp.get("persona") or persona_resp
    with _engine.begin() as conn:
        existing = conn.execute(
            text(f"SELECT {_AGENT_COLS} FROM dash.user_agents WHERE user_id = :u"), {"u": uid}
        ).fetchone()
        if existing:
            conn.execute(
                text("UPDATE dash.user_agents SET persona_json = CAST(:p AS jsonb), state = 'ready' "
                     "WHERE user_id = :u"),
                {"p": json.dumps(persona), "u": uid},
            )
            agent_id = str(existing[0])
        else:
            row = conn.execute(
                text("INSERT INTO dash.user_agents (user_id, persona_json, state) "
                     "VALUES (:u, CAST(:p AS jsonb), 'ready') RETURNING id"),
                {"u": uid, "p": json.dumps(persona)},
            ).fetchone()
            agent_id = str(row[0])
        _audit(conn, agent_id, uid, "bootstrap", {"signals_keys": list(signals.keys())})

    return {"agent_id": agent_id, "state": "ready", "persona": persona}


# ── 2. Get current user's agent ───────────────────────────────────────────

@router.get("/me")
def get_my_agent(request: Request):
    user = _get_user(request)
    uid = _uid(user)
    with _engine.connect() as conn:
        row = conn.execute(
            text(f"SELECT {_AGENT_COLS} FROM dash.user_agents WHERE user_id = :u"), {"u": uid}
        ).fetchone()
    if not row:
        raise HTTPException(404, "no agent for user")
    return _row_to_dict(row)


# ── 3. Train ──────────────────────────────────────────────────────────────

@router.post("/me/train")
async def train_my_agent(request: Request):
    user = _get_user(request)
    uid = _uid(user)
    with _engine.begin() as conn:
        row = conn.execute(text("SELECT id FROM dash.user_agents WHERE user_id = :u"), {"u": uid}).fetchone()
        if not row:
            raise HTTPException(404, "no agent for user")
        agent_id = str(row[0])
        conn.execute(
            text("UPDATE dash.user_agents SET state = 'training', last_sync = now() WHERE id = :a"),
            {"a": agent_id},
        )
        conn.execute(
            text("INSERT INTO dash.agent_memory_events (agent_id, event_type, payload) "
                 "VALUES (:a, 'action', CAST(:p AS jsonb))"),
            {"a": agent_id, "p": json.dumps({"kind": "train_started"})},
        )
        _audit(conn, agent_id, uid, "train", {})
    return StreamingResponse(iter([json.dumps({"status": "accepted", "agent_id": agent_id})]),
                             status_code=202, media_type="application/json")


# ── 4. Enable/disable ─────────────────────────────────────────────────────

@router.post("/me/enable")
async def enable_my_agent(request: Request):
    user = _get_user(request)
    uid = _uid(user)
    body = await request.json()
    enabled = bool(body.get("enabled"))
    with _engine.begin() as conn:
        row = conn.execute(text("SELECT id FROM dash.user_agents WHERE user_id = :u"), {"u": uid}).fetchone()
        if not row:
            raise HTTPException(404, "no agent for user")
        agent_id = str(row[0])
        conn.execute(text("UPDATE dash.user_agents SET enabled = :e WHERE id = :a"),
                     {"e": enabled, "a": agent_id})
        _audit(conn, agent_id, uid, "enable", {"enabled": enabled})
    return {"status": "ok", "enabled": enabled}


# ── 5. Delete ─────────────────────────────────────────────────────────────

@router.delete("/me")
async def delete_my_agent(request: Request):
    user = _get_user(request)
    uid = _uid(user)
    with _engine.begin() as conn:
        row = conn.execute(
            text("SELECT id, graph_id, zep_session_id FROM dash.user_agents WHERE user_id = :u"),
            {"u": uid},
        ).fetchone()
        if not row:
            raise HTTPException(404, "no agent for user")
        agent_id, graph_id, zep_session = str(row[0]), row[1], row[2]
        # cascade: memory + sims auto-cascade (sims SET NULL on agent_id)
        conn.execute(text("DELETE FROM dash.agent_simulations WHERE agent_id = :a"), {"a": agent_id})
        conn.execute(text("DELETE FROM dash.user_agents WHERE id = :a"), {"a": agent_id})
        _audit(conn, None, uid, "delete", {"agent_id": agent_id})

    # Best-effort remote cleanup
    client = get_client()
    try:
        if client.enabled and graph_id:
            await client._request("DELETE", f"/graph/{graph_id}")
        if client.enabled and zep_session:
            await client._request("DELETE", f"/session/{zep_session}")
    except Exception as e:
        logger.warning("mirofish cleanup failed: %s", e)
    return {"status": "ok"}


# ── 6. Memory ─────────────────────────────────────────────────────────────

@router.get("/me/memory")
def list_memory(request: Request, limit: int = 50, cursor: Optional[str] = None):
    user = _get_user(request)
    uid = _uid(user)
    limit = max(1, min(limit, 200))
    with _engine.connect() as conn:
        row = conn.execute(text("SELECT id FROM dash.user_agents WHERE user_id = :u"), {"u": uid}).fetchone()
        if not row:
            raise HTTPException(404, "no agent for user")
        agent_id = str(row[0])
        params = {"a": agent_id, "lim": limit}
        cursor_clause = ""
        if cursor:
            cursor_clause = " AND ts < :cur"
            params["cur"] = cursor
        rows = conn.execute(
            text(f"SELECT id, event_type, payload, ts FROM dash.agent_memory_events "
                 f"WHERE agent_id = :a{cursor_clause} ORDER BY ts DESC LIMIT :lim"),
            params,
        ).fetchall()
    events = [{"id": str(r[0]), "event_type": r[1], "payload": r[2], "ts": r[3].isoformat()} for r in rows]
    next_cursor = events[-1]["ts"] if len(events) == limit else None
    return {"events": events, "next_cursor": next_cursor}


# ── 7. Recommendations ────────────────────────────────────────────────────

@router.get("/me/recommendations")
async def recommendations(request: Request):
    user = _get_user(request)
    uid = _uid(user)
    with _engine.connect() as conn:
        row = conn.execute(text("SELECT id FROM dash.user_agents WHERE user_id = :u"), {"u": uid}).fetchone()
        if not row:
            raise HTTPException(404, "no agent for user")
        agent_id = str(row[0])
    try:
        results = await get_client().recall_memory(agent_id, "what should I do next", limit=3)
    except Exception as e:
        logger.warning("recall_memory failed: %s", e)
        results = []
    return {"recommendations": results[:3] if isinstance(results, list) else []}


# ── 8. Chat (streaming) ───────────────────────────────────────────────────

@router.post("/me/chat")
async def chat_my_agent(request: Request):
    user = _get_user(request)
    uid = _uid(user)
    body = await request.json()
    message = (body.get("message") or "").strip()
    if not message:
        raise HTTPException(400, "message required")

    with _engine.connect() as conn:
        row = conn.execute(text("SELECT id FROM dash.user_agents WHERE user_id = :u"), {"u": uid}).fetchone()
        if not row:
            raise HTTPException(404, "no agent for user")
        agent_id = str(row[0])

    async def gen():
        buf = []
        client = get_client()

        async def on_token(t: str):
            buf.append(t)
            yield_str = f"data: {json.dumps({'token': t})}\n\n"
            stream_chunks.append(yield_str)

        stream_chunks: list[str] = []
        try:
            await client.chat(agent_id, message, on_token)
        except Exception as e:
            stream_chunks.append(f"data: {json.dumps({'error': str(e)})}\n\n")
        for c in stream_chunks:
            yield c
        yield "data: [DONE]\n\n"

        # Log query event after stream completes
        full = "".join(buf)
        try:
            with _engine.begin() as conn:
                conn.execute(
                    text("INSERT INTO dash.agent_memory_events (agent_id, event_type, payload) "
                         "VALUES (:a, 'query', CAST(:p AS jsonb))"),
                    {"a": agent_id, "p": json.dumps({"q": message, "a": full[:4000]})},
                )
                _audit(conn, agent_id, uid, "chat", {"chars": len(full)})
        except Exception as e:
            logger.warning("chat log failed: %s", e)

    return StreamingResponse(gen(), media_type="text/event-stream")


# ── 9. Start simulation ───────────────────────────────────────────────────

@router.post("/sim")
async def start_sim(request: Request):
    user = _get_user(request)
    uid = _uid(user)
    body = await request.json()
    scenario = (body.get("scenario") or "").strip()
    if not scenario:
        raise HTTPException(400, "scenario required")
    horizon = body.get("horizon") or "30d"
    seed_tables = body.get("seed_tables") or []
    actors = int(body.get("actors") or 1)

    with _engine.connect() as conn:
        row = conn.execute(text("SELECT id, graph_id FROM dash.user_agents WHERE user_id = :u"),
                           {"u": uid}).fetchone()
        if not row:
            raise HTTPException(404, "no agent for user")
        agent_id, graph_id = str(row[0]), row[1] or ""

    try:
        resp = await get_client().run_simulation(graph_id, scenario, horizon, seed_tables, actors)
    except Exception as e:
        logger.exception("run_simulation failed")
        raise HTTPException(502, f"sim start failed: {e}")
    remote_sim_id = resp.get("sim_id") or ""

    with _engine.begin() as conn:
        ins = conn.execute(
            text("INSERT INTO dash.agent_simulations (agent_id, user_id, scenario, horizon, seed_tables, "
                 "status, result_json) VALUES (:a, :u, :s, :h, CAST(:st AS jsonb), :status, "
                 "CAST(:res AS jsonb)) RETURNING id"),
            {"a": agent_id, "u": uid, "s": scenario, "h": horizon,
             "st": json.dumps(seed_tables), "status": resp.get("status") or "queued",
             "res": json.dumps({"remote_sim_id": remote_sim_id})},
        ).fetchone()
        sim_id = str(ins[0])
        _audit(conn, agent_id, uid, "sim_start", {"sim_id": sim_id, "scenario": scenario})
    return {"sim_id": sim_id, "remote_sim_id": remote_sim_id, "status": resp.get("status") or "queued"}


# ── 10. Poll sim ──────────────────────────────────────────────────────────

@router.get("/sim/{sim_id}")
async def get_sim(sim_id: str, request: Request):
    user = _get_user(request)
    uid = _uid(user)
    with _engine.connect() as conn:
        row = conn.execute(
            text("SELECT id, agent_id, scenario, horizon, status, progress, result_json, "
                 "report_md, error FROM dash.agent_simulations WHERE id = :s AND user_id = :u"),
            {"s": sim_id, "u": uid},
        ).fetchone()
    if not row:
        raise HTTPException(404, "sim not found")

    remote_id = (row[6] or {}).get("remote_sim_id") if isinstance(row[6], dict) else None
    if remote_id and row[4] not in ("done", "failed"):
        try:
            remote = await get_client().get_sim_status(remote_id)
            new_status = remote.get("status") or row[4]
            new_progress = int(remote.get("progress") or row[5] or 0)
            new_report = remote.get("report_md") or row[7]
            new_result = remote.get("result_json") or {}
            new_error = remote.get("error")
            with _engine.begin() as conn:
                conn.execute(
                    text("UPDATE dash.agent_simulations SET status = :st, progress = :p, "
                         "result_json = CAST(:r AS jsonb), report_md = :rep, error = :err, "
                         "finished_at = CASE WHEN :st IN ('done','failed') THEN now() "
                         "ELSE finished_at END WHERE id = :s"),
                    {"st": new_status, "p": new_progress,
                     "r": json.dumps({"remote_sim_id": remote_id, **new_result}),
                     "rep": new_report, "err": new_error, "s": sim_id},
                )
                _audit(conn, row[1], uid, "sim_poll", {"sim_id": sim_id, "status": new_status})
            return {"sim_id": sim_id, "status": new_status, "progress": new_progress,
                    "report_md": new_report, "result_json": new_result, "error": new_error}
        except Exception as e:
            logger.warning("sim status poll failed: %s", e)

    return {"sim_id": sim_id, "status": row[4], "progress": row[5],
            "report_md": row[7], "result_json": row[6] or {}, "error": row[8]}


# ── 11. List sims ─────────────────────────────────────────────────────────

@router.get("/sim")
def list_sims(request: Request, limit: int = 50):
    user = _get_user(request)
    uid = _uid(user)
    limit = max(1, min(limit, 200))
    with _engine.connect() as conn:
        rows = conn.execute(
            text("SELECT id, scenario, horizon, status, progress, created_at, finished_at "
                 "FROM dash.agent_simulations WHERE user_id = :u ORDER BY created_at DESC LIMIT :lim"),
            {"u": uid, "lim": limit},
        ).fetchall()
    sims = [{"sim_id": str(r[0]), "scenario": r[1], "horizon": r[2], "status": r[3],
             "progress": r[4], "created_at": r[5].isoformat() if r[5] else None,
             "finished_at": r[6].isoformat() if r[6] else None} for r in rows]
    return {"sims": sims}


# ── 12. Agent templates (library) ─────────────────────────────────────────

@router.get("/templates")
async def list_templates(request: Request, source: str = "builtin"):
    """List available agent templates (builtin / globally-promoted by default)."""
    _ = _get_user(request)
    with _engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, name, description, purpose, base_agent, scoped_tools, fit_signals "
            "FROM dash.dash_custom_agents "
            "WHERE source = :s AND enabled = true AND is_promoted_global = true "
            "ORDER BY name"
        ), {"s": source}).fetchall()
    return {"templates": [
        {
            "id": r[0],
            "name": r[1],
            "description": r[2],
            "purpose": r[3],
            "base_agent": r[4],
            "scoped_tools": r[5] or [],
            "fit_signals": r[6] or {},
        }
        for r in rows
    ]}


@router.get("/templates/suggest")
async def suggest_templates(request: Request, slug: str, top_n: int = 3):
    """Suggest top-N templates ranked by project data fit. Fail-soft → []."""
    _ = _get_user(request)
    try:
        from dash.services.template_classifier import classify_for_project
        ranked = classify_for_project(slug, top_n=top_n)
    except Exception as e:
        logger.warning("template suggest failed for slug=%s: %s", slug, e)
        ranked = []
    return {"slug": slug, "suggestions": ranked}
