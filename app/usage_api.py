"""Usage API — super-admin unified usage/cost dashboard endpoints.

Reads the cross-source spine `public.v_usage_unified` (migrations 174/175) which
normalizes every per-call ledger — platform LLM, training, API-key gateway,
embeddings, embed widget — into one shape:

    src, ts, actor, store_id, model, tokens_in, tokens_out, cost_usd, latency_ms, status

Chat + training cost/tokens/latency come from `public.dash_traces` ROOT spans
(the real ledger). Deeper panels (performance, errors, tools, live) read
`dash_traces` directly; security reads `dash_security_events` + `dash_audit_log`.

All endpoints are super-admin gated and fail-soft.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Body, HTTPException, Query, Request
from sqlalchemy import text

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/usage", tags=["usage-admin"])

_SOURCES = {"platform", "training", "api_key", "embedding", "embed"}
_GROUPABLE = {"src", "model", "store_id", "actor", "status"}


# --- auth + db ---------------------------------------------------------------
def _get_user(request: Request) -> dict:
    user = getattr(getattr(request, "state", None), "user", None)
    if not user:
        raise HTTPException(401, "Not authenticated")
    return user


def _gate(request: Request):
    user = _get_user(request)
    if not user.get("is_super") and not user.get("is_super_admin"):
        raise HTTPException(403, "super-admin only")


def _engine():
    from db import get_sql_engine
    return get_sql_engine()


def _rows(query: str, params: dict | None = None) -> list:
    try:
        with _engine().connect() as conn:
            return conn.execute(text(query), params or {}).fetchall()
    except Exception as e:
        logger.debug("usage_api query failed: %s", e)
        return []


def _exec(query: str, params: dict | None = None) -> bool:
    try:
        with _engine().begin() as conn:
            conn.execute(text(query), params or {})
        return True
    except Exception as e:
        logger.warning("usage_api exec failed: %s", e)
        return False


# --- window + filters --------------------------------------------------------
def _window(frm: str | None, to: str | None) -> tuple[datetime, datetime]:
    def _p(s: str | None, default: datetime) -> datetime:
        if not s:
            return default
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            # Force tz-aware (UTC) — naive inputs would break arithmetic vs now().
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:
            return default
    now = datetime.now(timezone.utc)
    end = _p(to, now)
    start = _p(frm, end - timedelta(days=7))
    return start, end


def _filters(src, model, store, actor, status) -> tuple[str, dict]:
    clauses: list[str] = []
    params: dict = {}
    if src:
        wanted = [s.strip() for s in src.split(",") if s.strip() in _SOURCES]
        if wanted:
            clauses.append("src = ANY(:src)")
            params["src"] = wanted
    if model:
        clauses.append("model = :model"); params["model"] = model
    if store:
        clauses.append("store_id = :store"); params["store"] = store
    if actor:
        clauses.append("actor = :actor"); params["actor"] = actor
    if status:
        clauses.append("status = :status"); params["status"] = status
    frag = (" AND " + " AND ".join(clauses)) if clauses else ""
    return frag, params


# --- main rollup -------------------------------------------------------------
@router.get("")
@router.get("/")
def get_usage(
    request: Request,
    frm: str | None = Query(None, alias="from"),
    to: str | None = None,
    src: str | None = None, model: str | None = None, store: str | None = None,
    actor: str | None = None, status: str | None = None,
    granularity: str = "day", group_by: str = "src", limit: int = 100,
):
    _gate(request)
    start, end = _window(frm, to)
    frag, fp = _filters(src, model, store, actor, status)
    base = "FROM public.v_usage_unified WHERE ts >= :start AND ts < :end" + frag
    params = {"start": start, "end": end, **fp}
    gran = "hour" if granularity == "hour" else "day"
    gb = group_by if group_by in _GROUPABLE else "src"

    empty = {"window": {"from": start.isoformat(), "to": end.isoformat(), "granularity": gran},
             "totals": {"spend": 0.0, "requests": 0, "tokens_in": 0, "tokens_out": 0, "errors": 0},
             "prev": {"spend": 0.0, "requests": 0, "tokens": 0},
             "series": [], "model_series": [], "by_source": [], "by_model": [],
             "breakdown": [], "group_by": gb, "activity": [], "heatmap": [],
             "adoption": {}, "cost_per": {}}
    try:
        t = _rows("SELECT COALESCE(SUM(cost_usd),0), COUNT(*), COALESCE(SUM(tokens_in),0), "
                  "COALESCE(SUM(tokens_out),0), COUNT(*) FILTER (WHERE status='error') " + base, params)
        tot = t[0] if t else (0, 0, 0, 0, 0)

        # previous equal-length window
        plen = end - start
        pstart, pend = start - plen, start
        pt = _rows("SELECT COALESCE(SUM(cost_usd),0), COUNT(*), COALESCE(SUM(tokens_in+tokens_out),0) "
                   "FROM public.v_usage_unified WHERE ts >= :ps AND ts < :pe" + frag,
                   {"ps": pstart, "pe": pend, **fp})
        prev = pt[0] if pt else (0, 0, 0)

        series = _rows(f"SELECT date_trunc('{gran}', ts) b, COALESCE(SUM(cost_usd),0), COUNT(*), "
                       "COALESCE(SUM(tokens_in+tokens_out),0) " + base + " GROUP BY b ORDER BY b", params)
        mseries = _rows(f"SELECT date_trunc('{gran}', ts) b, COALESCE(model,'(none)'), "
                        "COALESCE(SUM(cost_usd),0), COUNT(*), COALESCE(SUM(tokens_in+tokens_out),0) "
                        + base + " GROUP BY b, model ORDER BY b", params)
        by_src = _rows("SELECT src, COUNT(*), COALESCE(SUM(tokens_in+tokens_out),0), COALESCE(SUM(cost_usd),0) "
                       + base + " GROUP BY src ORDER BY 4 DESC", params)
        by_model = _rows("SELECT COALESCE(model,'(none)'), COUNT(*), COALESCE(SUM(tokens_in+tokens_out),0), "
                         "COALESCE(SUM(cost_usd),0) " + base + " GROUP BY model ORDER BY 4 DESC LIMIT 25", params)
        brk = _rows(f"SELECT COALESCE({gb}::text,'(none)'), COUNT(*), COALESCE(SUM(tokens_in+tokens_out),0), "
                    "COALESCE(SUM(cost_usd),0), MAX(ts) " + base + " GROUP BY 1 ORDER BY 4 DESC LIMIT 50", params)
        act = _rows("SELECT ts, src, actor, store_id, model, (tokens_in+tokens_out), cost_usd, latency_ms, status "
                    + base + " ORDER BY ts DESC LIMIT :lim", {**params, "lim": max(1, min(limit, 500))})
        heat = _rows("SELECT EXTRACT(dow FROM ts)::int, EXTRACT(hour FROM ts)::int, COUNT(*), "
                     "COALESCE(SUM(cost_usd),0) " + base + " GROUP BY 1,2", params)
        # adoption
        adopt = _rows("SELECT COUNT(DISTINCT actor) FILTER (WHERE ts >= :d1) dau, "
                      "COUNT(DISTINCT actor) wau "
                      "FROM public.v_usage_unified WHERE ts >= :w7 AND actor <> 'system'",
                      {"d1": end - timedelta(days=1), "w7": end - timedelta(days=7)})
        au = _rows("SELECT COUNT(DISTINCT actor) " + base + " AND actor <> 'system'", params)
        tu = _rows("SELECT COUNT(*) FROM public.dash_users")

        spend = float(tot[0] or 0); reqs = int(tot[1] or 0); toks = int(tot[2] or 0) + int(tot[3] or 0)
        return {
            "window": empty["window"],
            "totals": {"spend": spend, "requests": reqs, "tokens_in": int(tot[2] or 0),
                       "tokens_out": int(tot[3] or 0), "errors": int(tot[4] or 0)},
            "prev": {"spend": float(prev[0] or 0), "requests": int(prev[1] or 0), "tokens": int(prev[2] or 0)},
            "series": [{"bucket": str(r[0]), "cost": float(r[1] or 0), "requests": int(r[2] or 0),
                        "tokens": int(r[3] or 0)} for r in series],
            "model_series": [{"bucket": str(r[0]), "model": r[1], "cost": float(r[2] or 0),
                              "requests": int(r[3] or 0), "tokens": int(r[4] or 0)} for r in mseries],
            "by_source": [{"src": r[0], "requests": int(r[1] or 0), "tokens": int(r[2] or 0),
                           "cost": float(r[3] or 0)} for r in by_src],
            "by_model": [{"model": r[0], "requests": int(r[1] or 0), "tokens": int(r[2] or 0),
                          "cost": float(r[3] or 0)} for r in by_model],
            "group_by": gb,
            "breakdown": [{"key": r[0], "requests": int(r[1] or 0), "tokens": int(r[2] or 0),
                           "cost": float(r[3] or 0), "last": str(r[4]) if r[4] else None} for r in brk],
            "activity": [{"ts": str(r[0]), "src": r[1], "actor": r[2], "store_id": r[3], "model": r[4],
                          "tokens": int(r[5] or 0), "cost": float(r[6] or 0),
                          "latency_ms": int(r[7] or 0), "status": r[8]} for r in act],
            "heatmap": [{"dow": int(r[0]), "hour": int(r[1]), "requests": int(r[2] or 0),
                         "cost": float(r[3] or 0)} for r in heat],
            "adoption": {"dau": int(adopt[0][0]) if adopt else 0, "wau": int(adopt[0][1]) if adopt else 0,
                         "active": int(au[0][0]) if au else 0, "total_users": int(tu[0][0]) if tu else 0},
            "cost_per": {"per_request": (spend / reqs) if reqs else 0.0,
                         "per_1k_tokens": (spend / (toks / 1000)) if toks else 0.0},
        }
    except Exception as e:
        logger.warning("GET /api/admin/usage failed: %s", e)
        out = dict(empty); out["error"] = str(e); return out


@router.get("/logins")
def get_logins(request: Request, frm: str | None = Query(None, alias="from"),
               to: str | None = None, limit: int = 50):
    _gate(request)
    start, end = _window(frm, to)
    try:
        rows = _rows(
            "SELECT u.username, u.last_login, COALESCE(u.role,'user'), "
            "COALESCE(x.req,0), COALESCE(x.cost,0), COALESCE(x.tok,0), x.last_use "
            "FROM public.dash_users u LEFT JOIN ("
            "  SELECT actor, COUNT(*) req, SUM(cost_usd) cost, SUM(tokens_in+tokens_out) tok, MAX(ts) last_use "
            "  FROM public.v_usage_unified WHERE ts >= :s AND ts < :e GROUP BY actor"
            ") x ON x.actor = u.username ORDER BY u.last_login DESC NULLS LAST LIMIT :l",
            {"s": start, "e": end, "l": max(1, min(limit, 500))})
        return {"logins": [{"username": r[0], "last_login": str(r[1]) if r[1] else None, "role": r[2],
                            "requests": int(r[3] or 0), "cost": float(r[4] or 0), "tokens": int(r[5] or 0),
                            "last_use": str(r[6]) if r[6] else None} for r in rows]}
    except Exception as e:
        return {"logins": [], "error": str(e)}


# --- performance (latency) ---------------------------------------------------
@router.get("/performance")
def get_performance(request: Request, frm: str | None = Query(None, alias="from"), to: str | None = None):
    _gate(request)
    start, end = _window(frm, to)
    base = "FROM public.v_usage_unified WHERE ts >= :s AND ts < :e AND latency_ms > 0"
    p = {"s": start, "e": end}
    try:
        ov = _rows("SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms), "
                   "percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms), "
                   "percentile_cont(0.99) WITHIN GROUP (ORDER BY latency_ms), "
                   "AVG(latency_ms), COUNT(*) " + base, p)
        bysrc = _rows("SELECT src, percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms), "
                      "percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms), "
                      "percentile_cont(0.99) WITHIN GROUP (ORDER BY latency_ms), COUNT(*) "
                      + base + " GROUP BY src ORDER BY 3 DESC", p)
        bymodel = _rows("SELECT COALESCE(model,'(none)'), percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms), "
                        "percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms), COUNT(*) "
                        + base + " GROUP BY model ORDER BY 3 DESC LIMIT 20", p)
        slow = _rows("SELECT ts, src, actor, model, latency_ms, cost_usd " + base
                     + " ORDER BY latency_ms DESC LIMIT 25", p)
        o = ov[0] if ov else (0, 0, 0, 0, 0)
        return {"overall": {"p50": int(o[0] or 0), "p95": int(o[1] or 0), "p99": int(o[2] or 0),
                            "avg": int(o[3] or 0), "n": int(o[4] or 0)},
                "by_source": [{"src": r[0], "p50": int(r[1] or 0), "p95": int(r[2] or 0),
                               "p99": int(r[3] or 0), "n": int(r[4] or 0)} for r in bysrc],
                "by_model": [{"model": r[0], "p50": int(r[1] or 0), "p95": int(r[2] or 0),
                              "n": int(r[3] or 0)} for r in bymodel],
                "slowest": [{"ts": str(r[0]), "src": r[1], "actor": r[2], "model": r[3],
                             "latency_ms": int(r[4] or 0), "cost": float(r[5] or 0)} for r in slow]}
    except Exception as e:
        return {"overall": {}, "by_source": [], "by_model": [], "slowest": [], "error": str(e)}


# --- errors ------------------------------------------------------------------
@router.get("/errors")
def get_errors(request: Request, frm: str | None = Query(None, alias="from"), to: str | None = None):
    _gate(request)
    start, end = _window(frm, to)
    base = "FROM public.v_usage_unified WHERE ts >= :s AND ts < :e"
    p = {"s": start, "e": end}
    try:
        tot = _rows("SELECT COUNT(*), COUNT(*) FILTER (WHERE status='error') " + base, p)
        n, ne = (int(tot[0][0]), int(tot[0][1])) if tot else (0, 0)
        bysrc = _rows("SELECT src, COUNT(*) FILTER (WHERE status='error'), COUNT(*) " + base
                      + " GROUP BY src ORDER BY 2 DESC", p)
        recent = _rows("SELECT started_at, kind, name, error FROM public.dash_traces "
                       "WHERE status='error' AND started_at >= :s AND started_at < :e "
                       "ORDER BY started_at DESC LIMIT 50", p)
        bycode = _rows("SELECT COALESCE(error,'unknown'), COUNT(*) FROM public.dash_traces "
                       "WHERE status='error' AND started_at >= :s AND started_at < :e "
                       "GROUP BY 1 ORDER BY 2 DESC LIMIT 20", p)
        return {"total": n, "errors": ne, "rate": (ne / n) if n else 0.0,
                "by_source": [{"src": r[0], "errors": int(r[1] or 0), "total": int(r[2] or 0)} for r in bysrc],
                "by_code": [{"code": (r[0] or "")[:140], "count": int(r[1] or 0)} for r in bycode],
                "recent": [{"ts": str(r[0]), "kind": r[1], "name": r[2], "error": (r[3] or "")[:300]}
                           for r in recent]}
    except Exception as e:
        return {"total": 0, "errors": 0, "rate": 0, "by_source": [], "by_code": [], "recent": [], "error": str(e)}


# --- tools (from trace child spans) ------------------------------------------
@router.get("/tools")
def get_tools(request: Request, frm: str | None = Query(None, alias="from"), to: str | None = None):
    _gate(request)
    start, end = _window(frm, to)
    try:
        rows = _rows(
            "SELECT split_part(name,'.',3) tool, COUNT(*), "
            "COUNT(*) FILTER (WHERE status='error'), "
            "percentile_cont(0.5) WITHIN GROUP (ORDER BY duration_ms), "
            "percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms) "
            "FROM public.dash_traces "
            "WHERE kind='chat' AND parent_id IS NOT NULL AND name LIKE 'chat.%.%' "
            "AND started_at >= :s AND started_at < :e "
            "GROUP BY tool HAVING split_part(name,'.',3) <> '' ORDER BY 2 DESC LIMIT 40",
            {"s": start, "e": end})
        return {"tools": [{"tool": r[0], "calls": int(r[1] or 0), "errors": int(r[2] or 0),
                           "err_pct": (100.0 * int(r[2] or 0) / int(r[1])) if r[1] else 0.0,
                           "p50_ms": int(r[3] or 0), "p95_ms": int(r[4] or 0)} for r in rows]}
    except Exception as e:
        return {"tools": [], "error": str(e)}


# --- security / guardrail ----------------------------------------------------
@router.get("/security")
def get_security(request: Request, frm: str | None = Query(None, alias="from"), to: str | None = None):
    _gate(request)
    start, end = _window(frm, to)
    p = {"s": start, "e": end}
    try:
        by_kind = _rows("SELECT kind, severity, COUNT(*) FROM public.dash_security_events "
                        "WHERE ts >= :s AND ts < :e GROUP BY 1,2 ORDER BY 3 DESC", p)
        recent = _rows("SELECT ts, kind, severity, service_account, store_id, detail "
                       "FROM public.dash_security_events WHERE ts >= :s AND ts < :e "
                       "ORDER BY ts DESC LIMIT 60", p)
        rl = _rows("SELECT COUNT(*) FROM public.dash_apigw_usage "
                   "WHERE status='rate_limited' AND ts >= :s AND ts < :e", p)
        auth = _rows("SELECT service_account, COUNT(*) FROM public.dash_security_events "
                     "WHERE kind='auth_fail' AND ts >= :s AND ts < :e GROUP BY 1 ORDER BY 2 DESC LIMIT 20", p)
        return {"by_kind": [{"kind": r[0], "severity": r[1], "count": int(r[2] or 0)} for r in by_kind],
                "recent": [{"ts": str(r[0]), "kind": r[1], "severity": r[2], "service_account": r[3],
                            "store_id": r[4], "detail": r[5]} for r in recent],
                "rate_limited": int(rl[0][0]) if rl else 0,
                "auth_failures": [{"who": r[0], "count": int(r[1] or 0)} for r in auth]}
    except Exception as e:
        return {"by_kind": [], "recent": [], "rate_limited": 0, "auth_failures": [], "error": str(e)}


# --- entity drilldown --------------------------------------------------------
@router.get("/entity")
def get_entity(request: Request, type: str, id: str,
               frm: str | None = Query(None, alias="from"), to: str | None = None):
    _gate(request)
    start, end = _window(frm, to)
    col = {"actor": "actor", "store": "store_id", "model": "model", "user": "actor"}.get(type, "actor")
    base = f"FROM public.v_usage_unified WHERE ts >= :s AND ts < :e AND {col} = :id"
    p = {"s": start, "e": end, "id": id}
    try:
        t = _rows("SELECT COALESCE(SUM(cost_usd),0), COUNT(*), COALESCE(SUM(tokens_in+tokens_out),0), "
                  "COUNT(*) FILTER (WHERE status='error'), MAX(ts) " + base, p)
        tt = t[0] if t else (0, 0, 0, 0, None)
        bym = _rows("SELECT COALESCE(model,'(none)'), COUNT(*), COALESCE(SUM(cost_usd),0) "
                    + base + " GROUP BY 1 ORDER BY 3 DESC LIMIT 15", p)
        ser = _rows("SELECT date_trunc('day', ts), COALESCE(SUM(cost_usd),0), COUNT(*) "
                    + base + " GROUP BY 1 ORDER BY 1", p)
        act = _rows("SELECT ts, src, model, (tokens_in+tokens_out), cost_usd, latency_ms, status "
                    + base + " ORDER BY ts DESC LIMIT 50", p)
        return {"type": type, "id": id,
                "totals": {"spend": float(tt[0] or 0), "requests": int(tt[1] or 0), "tokens": int(tt[2] or 0),
                           "errors": int(tt[3] or 0), "last": str(tt[4]) if tt[4] else None},
                "by_model": [{"model": r[0], "requests": int(r[1] or 0), "cost": float(r[2] or 0)} for r in bym],
                "series": [{"bucket": str(r[0]), "cost": float(r[1] or 0), "requests": int(r[2] or 0)} for r in ser],
                "activity": [{"ts": str(r[0]), "src": r[1], "model": r[2], "tokens": int(r[3] or 0),
                              "cost": float(r[4] or 0), "latency_ms": int(r[5] or 0), "status": r[6]} for r in act]}
    except Exception as e:
        return {"type": type, "id": id, "totals": {}, "by_model": [], "series": [], "activity": [], "error": str(e)}


# --- chat bodies (gated by APIGW_LOG_BODIES at write time) -------------------
@router.get("/messages")
def get_messages(request: Request, key_id: int | None = None, store: str | None = None,
                 frm: str | None = Query(None, alias="from"), to: str | None = None, limit: int = 100):
    _gate(request)
    start, end = _window(frm, to)
    cl = ["ts >= :s", "ts < :e"]; p = {"s": start, "e": end}
    if key_id is not None:
        cl.append("key_id = :k"); p["k"] = key_id
    if store:
        cl.append("store_id = :st"); p["st"] = store
    p["l"] = max(1, min(limit, 500))
    try:
        rows = _rows("SELECT ts, session_id, service_account, store_id, role, content, masked "
                     "FROM public.dash_apigw_messages WHERE " + " AND ".join(cl)
                     + " ORDER BY ts DESC, id DESC LIMIT :l", p)
        return {"enabled": True,
                "messages": [{"ts": str(r[0]), "session_id": r[1], "service_account": r[2], "store_id": r[3],
                              "role": r[4], "content": r[5], "masked": bool(r[6])} for r in rows]}
    except Exception as e:
        return {"enabled": False, "messages": [], "error": str(e)}


@router.get("/key/{name}")
def get_key_detail(request: Request, name: str,
                   frm: str | None = Query(None, alias="from"), to: str | None = None,
                   limit: int = 100):
    _gate(request)
    start, end = _window(frm, to)
    p = {"n": name, "s": start, "e": end, "l": max(1, min(limit, 500))}
    # resolve the canonical username (with-or-without svc: prefix)
    u = _rows("SELECT username, scope_mode, COALESCE(NULLIF(store_id,''), NULLIF(store_ids,''),'-'), "
              "is_active, to_char(created_at,'YYYY-MM-DD') "
              "FROM public.dash_users WHERE (username = :n OR username = 'svc:' || :n) "
              "AND api_key IS NOT NULL LIMIT 1", {"n": name})
    if not u:
        return {"key": name, "found": False, "header": {}, "questions": [], "messages_enabled": False}
    sa = u[0][0]  # canonical service_account incl any svc: prefix
    p["sa"] = sa
    # aggregate usage for this key (windowed)
    g = _rows(
        "SELECT COUNT(*), COALESCE(SUM(total_tokens),0), COALESCE(SUM(prompt_tokens),0), "
        "COALESCE(SUM(completion_tokens),0), COALESCE(SUM(cost_usd),0), COALESCE(AVG(latency_ms),0), "
        "COALESCE(SUM((status IS NOT NULL AND status <> 'ok')::int),0), "
        "COUNT(DISTINCT session_id), COALESCE(SUM(streamed::int),0), MAX(ts) "
        "FROM public.dash_apigw_usage WHERE service_account = :sa AND ts >= :s AND ts < :e",
        {"sa": sa, "s": start, "e": end})
    gg = g[0] if g else (0, 0, 0, 0, 0, 0, 0, 0, 0, None)
    calls = int(gg[0] or 0)
    header = {
        "scope": u[0][1], "stores": u[0][2], "active": bool(u[0][3]), "minted": u[0][4],
        "calls": calls, "tokens": int(gg[1] or 0), "prompt_tokens": int(gg[2] or 0),
        "completion_tokens": int(gg[3] or 0), "cost": float(gg[4] or 0),
        "avg_latency_ms": int(gg[5] or 0), "errors": int(gg[6] or 0),
        "sessions": int(gg[7] or 0),
        "stream_pct": int(round(100.0 * int(gg[8] or 0) / calls)) if calls else 0,
        "last_used": str(gg[9]) if gg[9] else None,
    }
    # per-question rows: pair user+assistant by session from messages, enrich w/ usage by session
    questions = []
    msgs = _rows(
        "SELECT session_id, "
        "MAX(CASE WHEN role='user' THEN content END) AS q, "
        "MAX(CASE WHEN role='assistant' THEN content END) AS a, "
        "BOOL_OR(masked) AS masked, MIN(ts) AS ts "
        "FROM public.dash_apigw_messages "
        "WHERE service_account = :sa AND ts >= :s AND ts < :e "
        "GROUP BY session_id ORDER BY MIN(ts) DESC LIMIT :l", p)
    messages_enabled = len(msgs) > 0
    # usage-by-session lookup for tokens/latency/status (works even if no message bodies)
    usess = _rows(
        "SELECT session_id, COALESCE(SUM(total_tokens),0), COALESCE(AVG(latency_ms),0), "
        "MAX(CASE WHEN status IS NOT NULL AND status <> 'ok' THEN status END), MAX(ts) "
        "FROM public.dash_apigw_usage WHERE service_account = :sa AND ts >= :s AND ts < :e "
        "AND session_id IS NOT NULL GROUP BY session_id", {"sa": sa, "s": start, "e": end})
    umap = {r[0]: r for r in usess}
    if messages_enabled:
        for r in msgs:
            sid = r[0]; ux = umap.get(sid)
            questions.append({
                "ts": str(r[4]) if r[4] else None, "session_id": sid,
                "question": r[1], "answer": r[2], "masked": bool(r[3]),
                "tokens": int(ux[1]) if ux else 0,
                "latency_ms": int(ux[2]) if ux else 0,
                "status": (ux[3] if ux else None),
                "error": (ux[3] if ux and ux[3] else None),
            })
    else:
        # no chat bodies logged — fall back to usage-only rows so the UI still shows activity
        for r in sorted(usess, key=lambda x: (x[4] is not None, x[4]), reverse=True)[:p["l"]]:
            questions.append({
                "ts": str(r[4]) if r[4] else None, "session_id": r[0],
                "question": None, "answer": None, "masked": True,
                "tokens": int(r[1] or 0), "latency_ms": int(r[2] or 0),
                "status": r[3], "error": r[3],
            })
    return {"key": sa.replace("svc:", ""), "found": True, "header": header,
            "questions": questions, "messages_enabled": messages_enabled}


# --- live / active now -------------------------------------------------------
@router.get("/live")
def get_live(request: Request):
    _gate(request)
    try:
        active = _rows(
            "WITH recent AS (SELECT DISTINCT session_id FROM public.dash_sse_audit "
            "  WHERE ts >= now() - interval '5 minutes' "
            "  AND event_name IN ('ReasoningStep','ToolCallStarted','ToolCallCompleted')), "
            "done AS (SELECT DISTINCT session_id FROM public.dash_sse_audit "
            "  WHERE event_name IN ('TeamRunCompleted','TeamRunContent') "
            "  AND ts >= now() - interval '5 minutes') "
            "SELECT r.session_id, (SELECT MAX(ts) FROM public.dash_sse_audit WHERE session_id=r.session_id) "
            "FROM recent r LEFT JOIN done d ON r.session_id=d.session_id "
            "WHERE d.session_id IS NULL ORDER BY 2 DESC LIMIT 25")
        tpm = _rows("SELECT COALESCE(SUM(tokens),0), COUNT(*) FILTER (WHERE status='running') "
                    "FROM public.dash_traces WHERE started_at >= now() - interval '60 seconds'")
        recent = _rows("SELECT ts, src, actor, model, (tokens_in+tokens_out), cost_usd, status "
                       "FROM public.v_usage_unified ORDER BY ts DESC LIMIT 20")
        return {"active_sessions": [{"session_id": r[0], "last": str(r[1]) if r[1] else None} for r in active],
                "tokens_last_min": int(tpm[0][0]) if tpm else 0,
                "running": int(tpm[0][1]) if tpm else 0,
                "recent": [{"ts": str(r[0]), "src": r[1], "actor": r[2], "model": r[3], "tokens": int(r[4] or 0),
                            "cost": float(r[5] or 0), "status": r[6]} for r in recent]}
    except Exception as e:
        return {"active_sessions": [], "tokens_last_min": 0, "running": 0, "recent": [], "error": str(e)}


# --- budget ------------------------------------------------------------------
@router.get("/budget")
def get_budget(request: Request):
    _gate(request)
    try:
        b = _rows("SELECT daily_usd, monthly_usd FROM public.dash_usage_budget WHERE id=1")
        daily = float(b[0][0]) if b else 0.0
        monthly = float(b[0][1]) if b else 0.0
        today = _rows("SELECT COALESCE(SUM(cost_usd),0) FROM public.v_usage_unified "
                      "WHERE ts >= date_trunc('day', now())")
        mtd = _rows("SELECT COALESCE(SUM(cost_usd),0) FROM public.v_usage_unified "
                    "WHERE ts >= date_trunc('month', now())")
        ts = float(today[0][0]) if today else 0.0
        ms = float(mtd[0][0]) if mtd else 0.0
        from datetime import datetime as _dt
        now = datetime.now(timezone.utc)
        dom = now.day
        proj = (ms / dom * 30) if dom else ms
        return {"daily_usd": daily, "monthly_usd": monthly, "today_spend": ts, "mtd_spend": ms,
                "projected_month": proj,
                "daily_pct": (ts / daily * 100) if daily else 0.0,
                "monthly_pct": (ms / monthly * 100) if monthly else 0.0,
                "over_daily": bool(daily and ts > daily), "over_monthly": bool(monthly and ms > monthly)}
    except Exception as e:
        return {"daily_usd": 0, "monthly_usd": 0, "today_spend": 0, "mtd_spend": 0, "error": str(e)}


@router.post("/budget")
def set_budget(request: Request, payload: dict = Body(...)):
    _gate(request)
    try:
        daily = float(payload.get("daily_usd") or 0)
        monthly = float(payload.get("monthly_usd") or 0)
    except Exception:
        raise HTTPException(400, "daily_usd / monthly_usd must be numbers")
    ok = _exec("UPDATE public.dash_usage_budget SET daily_usd=:d, monthly_usd=:m, updated_at=now() WHERE id=1",
               {"d": daily, "m": monthly})
    # Fire a budget alert if MTD already exceeds the new monthly target.
    if ok and monthly:
        mtd = _rows("SELECT COALESCE(SUM(cost_usd),0) FROM public.v_usage_unified WHERE ts >= date_trunc('month', now())")
        ms = float(mtd[0][0]) if mtd else 0.0
        if ms > monthly:
            _exec("INSERT INTO dash.tel_alerts (severity, rule_name, owner, detail) "
                  "VALUES ('CRIT','cost.monthly_budget_exceeded','finops', :d)",
                  {"d": f"MTD ${ms:.2f} exceeds ${monthly:.2f} budget"})
    return {"ok": ok, "daily_usd": daily, "monthly_usd": monthly}


# --- per-outlet stats (gateway provision view) --------------------------------
@router.get("/outlet-stats")
def get_outlet_stats(request: Request, days: int = Query(7)):
    """Return windowed call/error/token/last_used stats per svc:outlet-* account.

    Groups dash_apigw_usage by service_account for accounts matching the
    svc:outlet-* naming convention. Strips the 'svc:outlet-' prefix to expose
    the site_code as the key.  Fail-soft — returns empty list on any error.
    """
    _gate(request)
    days = max(1, min(days, 90))
    try:
        rows = _rows(
            "SELECT service_account, "
            "  COUNT(*)                                          AS reqs, "
            "  COUNT(*) FILTER (WHERE status <> 'ok')           AS errors, "
            "  COALESCE(SUM(total_tokens), 0)                   AS tokens, "
            "  MAX(ts)                                           AS last_used "
            "FROM public.dash_apigw_usage "
            "WHERE service_account LIKE 'svc:outlet-%' "
            "  AND ts >= now() - (:days * interval '1 day') "
            "GROUP BY service_account",
            {"days": days},
        )
        return {
            "days": days,
            "stats": [
                {
                    "outlet": (r[0] or "").removeprefix("svc:outlet-"),
                    "reqs": int(r[1] or 0),
                    "errors": int(r[2] or 0),
                    "tokens": int(r[3] or 0),
                    "last_used": str(r[4]) if r[4] else None,
                }
                for r in rows
            ],
        }
    except Exception as e:
        logger.debug("outlet-stats failed: %s", e)
        return {"days": days, "stats": [], "error": str(e)}


# --- invoice rollup (frontend → CSV) -----------------------------------------
@router.get("/invoice")
def get_invoice(request: Request, group: str = "store",
                frm: str | None = Query(None, alias="from"), to: str | None = None):
    _gate(request)
    start, end = _window(frm, to)
    col = "store_id" if group == "store" else "actor"
    try:
        rows = _rows(f"SELECT COALESCE({col},'(none)'), COUNT(*), COALESCE(SUM(tokens_in+tokens_out),0), "
                     "COALESCE(SUM(cost_usd),0) FROM public.v_usage_unified "
                     "WHERE ts >= :s AND ts < :e GROUP BY 1 ORDER BY 4 DESC",
                     {"s": start, "e": end})
        return {"group": group, "window": {"from": start.isoformat(), "to": end.isoformat()},
                "rows": [{"key": r[0], "requests": int(r[1] or 0), "tokens": int(r[2] or 0),
                          "cost": float(r[3] or 0)} for r in rows]}
    except Exception as e:
        return {"group": group, "rows": [], "error": str(e)}


# --- OpenRouter-style Gateway Analytics --------------------------------------
def _range_window(range_: str):
    # returns (start, end, prev_start, prev_end) tz-aware UTC
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    days = {"24h": 1, "7d": 7, "30d": 30}.get(range_, 7)
    start = now - timedelta(days=days)
    return start, now, start - timedelta(days=days), start


@router.get("/gateway-overview")
def gateway_overview(request: Request, range: str = "7d", gran: str = "day",
                     key: str | None = None, model: str | None = None,
                     store: str | None = None):
    _gate(request)
    start, end, pstart, pend = _range_window(range)
    bucket = "hour" if gran == "hour" else "day"
    # build optional filters (match key with/without svc: prefix)
    filt = ["ts >= :s", "ts < :e"]; p = {"s": start, "e": end}
    if key:   filt.append("(service_account = :k OR service_account = 'svc:' || :k)"); p["k"] = key
    if model: filt.append("model = :m"); p["m"] = model
    if store: filt.append("store_id = :st"); p["st"] = store
    W = " AND ".join(filt)
    pfilt = ["ts >= :ps", "ts < :pe"] + filt[2:]; pp = dict(p); pp["ps"] = pstart; pp["pe"] = pend; pp.pop("s", None); pp.pop("e", None)
    PW = " AND ".join(pfilt)

    try:
        # KPI (current + prev for delta)
        def _kpi(where, params):
            r = _rows(f"SELECT COUNT(*), COALESCE(SUM(total_tokens),0), COALESCE(SUM(prompt_tokens),0), "
                      f"COALESCE(SUM(completion_tokens),0), COALESCE(SUM(cost_usd),0), "
                      f"COALESCE(AVG(latency_ms) FILTER (WHERE latency_ms IS NOT NULL),0), "
                      f"COALESCE(SUM((status IS NOT NULL AND status <> 'ok')::int),0), "
                      f"COALESCE(SUM(streamed::int),0), COUNT(DISTINCT service_account), "
                      f"COALESCE(SUM((request_type='cache_hit')::int),0) "
                      f"FROM public.dash_apigw_usage WHERE {where}", params)
            return r[0] if r else (0,) * 10
        c = _kpi(W, p); pv = _kpi(PW, pp)
        # percentiles
        pct = _rows(f"SELECT percentile_disc(0.5) WITHIN GROUP (ORDER BY latency_ms), "
                    f"percentile_disc(0.9) WITHIN GROUP (ORDER BY latency_ms), "
                    f"percentile_disc(0.95) WITHIN GROUP (ORDER BY latency_ms), "
                    f"percentile_disc(0.99) WITHIN GROUP (ORDER BY latency_ms) "
                    f"FROM public.dash_apigw_usage WHERE {W} AND latency_ms IS NOT NULL", p)
        pr = pct[0] if pct else (None, None, None, None)
        reqs = int(c[0] or 0)
        total_keys = _rows("SELECT COUNT(*) FROM public.dash_users WHERE api_key IS NOT NULL")
        kpi = {
            "requests": reqs, "requests_prev": int(pv[0] or 0),
            "tokens": int(c[1] or 0), "tokens_prev": int(pv[1] or 0),
            "prompt_tokens": int(c[2] or 0), "completion_tokens": int(c[3] or 0),
            "cost": float(c[4] or 0), "cost_prev": float(pv[4] or 0),
            "avg_latency_ms": int(c[5] or 0),
            "p50": int(pr[0] or 0), "p90": int(pr[1] or 0), "p95": int(pr[2] or 0), "p99": int(pr[3] or 0),
            "errors": int(c[6] or 0), "error_rate": (float(c[6] or 0) / reqs if reqs else 0.0),
            "stream_pct": int(round(100.0 * int(c[7] or 0) / reqs)) if reqs else 0,
            "cache_hits": int(c[9] or 0), "cache_hit_rate": (float(c[9] or 0) / reqs if reqs else 0.0),
            "active_keys": int(c[8] or 0),
            "total_keys": int(total_keys[0][0]) if total_keys else 0,
        }
        # time series (bucketed)
        series = [{"bucket": str(r[0]), "requests": int(r[1] or 0), "prompt_tokens": int(r[2] or 0),
                   "completion_tokens": int(r[3] or 0), "tokens": int(r[4] or 0), "cost": float(r[5] or 0),
                   "avg_latency_ms": int(r[6] or 0), "errors": int(r[7] or 0)}
                  for r in _rows(
            f"SELECT date_trunc('{bucket}', ts) b, COUNT(*), COALESCE(SUM(prompt_tokens),0), "
            f"COALESCE(SUM(completion_tokens),0), COALESCE(SUM(total_tokens),0), COALESCE(SUM(cost_usd),0), "
            f"COALESCE(AVG(latency_ms) FILTER (WHERE latency_ms IS NOT NULL),0), "
            f"COALESCE(SUM((status IS NOT NULL AND status<>'ok')::int),0) "
            f"FROM public.dash_apigw_usage WHERE {W} GROUP BY b ORDER BY b", p)]
        # by model
        by_model = [{"model": r[0] or "(none)", "requests": int(r[1] or 0), "prompt_tokens": int(r[2] or 0),
                     "completion_tokens": int(r[3] or 0), "tokens": int(r[4] or 0), "cost": float(r[5] or 0),
                     "avg_latency_ms": int(r[6] or 0), "errors": int(r[7] or 0)}
                    for r in _rows(
            f"SELECT model, COUNT(*), COALESCE(SUM(prompt_tokens),0), COALESCE(SUM(completion_tokens),0), "
            f"COALESCE(SUM(total_tokens),0), COALESCE(SUM(cost_usd),0), "
            f"COALESCE(AVG(latency_ms) FILTER (WHERE latency_ms IS NOT NULL),0), "
            f"COALESCE(SUM((status IS NOT NULL AND status<>'ok')::int),0) "
            f"FROM public.dash_apigw_usage WHERE {W} GROUP BY model ORDER BY 2 DESC", p)]
        # by key (clickable in UI → existing drill-down). strip svc: prefix in output.
        by_key = [{"key": (r[0] or "").replace("svc:", ""), "scope": r[1] or "global", "store": r[2] or "",
                   "requests": int(r[3] or 0), "tokens": int(r[4] or 0), "cost": float(r[5] or 0),
                   "avg_latency_ms": int(r[6] or 0), "errors": int(r[7] or 0),
                   "stream_pct": int(round(100.0 * int(r[8] or 0) / int(r[3]))) if r[3] else 0,
                   "last": str(r[9]) if r[9] else None}
                  for r in _rows(
            f"SELECT service_account, MAX(scope_mode), MAX(store_id), COUNT(*), COALESCE(SUM(total_tokens),0), "
            f"COALESCE(SUM(cost_usd),0), COALESCE(AVG(latency_ms) FILTER (WHERE latency_ms IS NOT NULL),0), "
            f"COALESCE(SUM((status IS NOT NULL AND status<>'ok')::int),0), COALESCE(SUM(streamed::int),0), MAX(ts) "
            f"FROM public.dash_apigw_usage WHERE {W} GROUP BY service_account ORDER BY 4 DESC LIMIT 50", p)]
        # top key per store — computed Python-side (robust; avoids the fragile ts→u2.ts replace gotcha)
        topkey_map: dict = {}
        for r in _rows(
                f"SELECT store_id, service_account, COUNT(*) c "
                f"FROM public.dash_apigw_usage WHERE {W} AND store_id IS NOT NULL "
                f"GROUP BY store_id, service_account ORDER BY store_id, c DESC", p):
            sid = r[0]
            if sid not in topkey_map:  # first row per store = highest count (ordered)
                topkey_map[sid] = (r[1] or "").replace("svc:", "")
        # by store/outlet
        by_store = [{"store": r[0] or "(none)", "requests": int(r[1] or 0), "tokens": int(r[2] or 0),
                     "cost": float(r[3] or 0), "errors": int(r[4] or 0),
                     "top_key": topkey_map.get(r[0], ""), "last": str(r[5]) if r[5] else None}
                    for r in _rows(
            f"SELECT store_id, COUNT(*), COALESCE(SUM(total_tokens),0), COALESCE(SUM(cost_usd),0), "
            f"COALESCE(SUM((status IS NOT NULL AND status<>'ok')::int),0), MAX(ts) "
            f"FROM public.dash_apigw_usage WHERE {W} AND store_id IS NOT NULL GROUP BY store_id ORDER BY 2 DESC LIMIT 50", p)]
        # latency histogram (fixed buckets, seconds)
        lat = _rows(
            f"SELECT CASE WHEN latency_ms < 2000 THEN '<2s' WHEN latency_ms < 10000 THEN '2-10s' "
            f"WHEN latency_ms < 30000 THEN '10-30s' WHEN latency_ms < 60000 THEN '30-60s' ELSE '>60s' END b, COUNT(*) "
            f"FROM public.dash_apigw_usage WHERE {W} AND latency_ms IS NOT NULL GROUP BY b", p)
        order = ['<2s', '2-10s', '10-30s', '30-60s', '>60s']; lmap = {r[0]: int(r[1]) for r in lat}
        ltot = sum(lmap.values()) or 1
        latency_hist = [{"bucket": b, "count": lmap.get(b, 0), "pct": round(100.0 * lmap.get(b, 0) / ltot, 1)} for b in order]
        # errors panel
        by_status = [{"status": r[0] or "unknown", "count": int(r[1])} for r in _rows(
            f"SELECT status, COUNT(*) FROM public.dash_apigw_usage WHERE {W} AND status IS NOT NULL AND status<>'ok' GROUP BY status ORDER BY 2 DESC", p)]
        recent_err = [{"ts": str(r[0]), "key": (r[1] or '').replace('svc:', ''), "store": r[2], "status": r[3]} for r in _rows(
            f"SELECT ts, service_account, store_id, status FROM public.dash_apigw_usage WHERE {W} AND status IS NOT NULL AND status<>'ok' ORDER BY ts DESC LIMIT 10", p)]
        return {"range": range, "gran": bucket, "kpi": kpi, "series": series, "by_model": by_model,
                "by_key": by_key, "by_store": by_store, "latency_hist": latency_hist,
                "token_split": {"prompt": kpi["prompt_tokens"], "completion": kpi["completion_tokens"]},
                "errors": {"rate": kpi["error_rate"], "total": kpi["errors"], "by_status": by_status, "recent": recent_err}}
    except Exception as e:
        logger.warning("GET /api/admin/usage/gateway-overview failed: %s", e)
        return {"range": range, "gran": bucket, "kpi": {}, "series": [], "by_model": [],
                "by_key": [], "by_store": [], "latency_hist": [],
                "token_split": {"prompt": 0, "completion": 0},
                "errors": {"rate": 0.0, "total": 0, "by_status": [], "recent": []}, "error": str(e)}


@router.get("/embed-overview")
def embed_overview(request: Request, days: int = 7, embed_id: str | None = None,
                   bucket: str = "day"):
    """Embed widget monitoring — aggregates public.dash_embed_calls for citypharma.

    No token/cost data exists for embeds (do NOT report tokens/cost). Returns
    request/latency/error KPIs, bucketed activity series, latency histogram,
    per-widget rollup (joined to dash_agent_embeds for name + store), top users,
    and origins.
    """
    _gate(request)
    try:
        from dash.single_agent import locked_slug
        slug = locked_slug()
    except Exception:
        slug = "citypharma"
    now = datetime.now(timezone.utc)
    try:
        days = max(1, int(days))
    except Exception:
        days = 7
    start = now - timedelta(days=days)
    bkt = "hour" if bucket == "hour" else "day"

    # scope calls to embeds bound to this project; optional single-widget filter
    filt = ["c.ts >= :s", "c.ts < :e", "e.project_slug = :slug"]
    p: dict = {"s": start, "e": now, "slug": slug}
    if embed_id:
        filt.append("c.embed_id = :eid"); p["eid"] = embed_id
    W = " AND ".join(filt)
    # base join: calls -> embeds (so we can scope by project + enrich name/store)
    FROM = ("FROM public.dash_embed_calls c "
            "JOIN public.dash_agent_embeds e ON e.embed_id = c.embed_id")

    try:
        # --- KPI ---
        k = _rows(
            f"SELECT COUNT(*), COALESCE(SUM(c.success::int),0), "
            f"COALESCE(AVG(c.latency_ms) FILTER (WHERE c.latency_ms IS NOT NULL),0), "
            f"COUNT(DISTINCT c.external_user) FILTER (WHERE c.external_user IS NOT NULL), "
            f"COUNT(DISTINCT c.session_token) FILTER (WHERE c.session_token IS NOT NULL), "
            f"COALESCE(AVG(c.response_chars) FILTER (WHERE c.response_chars IS NOT NULL),0), "
            f"COUNT(DISTINCT c.embed_id) "
            f"{FROM} WHERE {W}", p)
        kr = k[0] if k else (0, 0, 0, 0, 0, 0, 0)
        reqs = int(kr[0] or 0)
        ok = int(kr[1] or 0)
        # percentiles (all calls with a latency value)
        pct = _rows(
            f"SELECT percentile_disc(0.5) WITHIN GROUP (ORDER BY c.latency_ms), "
            f"percentile_disc(0.95) WITHIN GROUP (ORDER BY c.latency_ms), "
            f"percentile_disc(0.99) WITHIN GROUP (ORDER BY c.latency_ms) "
            f"{FROM} WHERE {W} AND c.latency_ms IS NOT NULL", p)
        pr = pct[0] if pct else (None, None, None)
        # active embeds = distinct embeds with a call in window / total enabled embeds bound to a store
        total_bound = _rows(
            "SELECT COUNT(*) FROM public.dash_agent_embeds "
            "WHERE project_slug = :slug AND enabled IS TRUE AND bound_scope_id IS NOT NULL",
            {"slug": slug})
        total_embeds = int(total_bound[0][0]) if total_bound else 0
        active_embeds = int(kr[6] or 0)
        kpi = {
            "requests": reqs,
            "error_pct": round(100.0 * (1 - (ok / reqs)), 2) if reqs else 0.0,
            "latency_avg_ms": int(kr[2] or 0),
            "latency_p50_ms": int(pr[0] or 0),
            "latency_p95_ms": int(pr[1] or 0),
            "latency_p99_ms": int(pr[2] or 0),
            "uniq_users": int(kr[3] or 0),
            "uniq_sessions": int(kr[4] or 0),
            "active_embeds": f"{active_embeds}/{total_embeds}",
            "avg_resp_chars": int(kr[5] or 0),
        }

        # --- time series (bucketed) ---
        series = [{"t": str(r[0]), "requests": int(r[1] or 0), "errors": int(r[2] or 0),
                   "p95_ms": int(r[3] or 0)}
                  for r in _rows(
            f"SELECT date_trunc('{bkt}', c.ts) b, COUNT(*), "
            f"COALESCE(SUM((NOT c.success)::int),0), "
            f"COALESCE(percentile_disc(0.95) WITHIN GROUP (ORDER BY c.latency_ms),0) "
            f"{FROM} WHERE {W} GROUP BY b ORDER BY b", p)]

        # --- latency histogram (fixed buckets, seconds) ---
        lat = _rows(
            f"SELECT CASE WHEN c.latency_ms < 2000 THEN '<2s' WHEN c.latency_ms < 10000 THEN '2-10s' "
            f"WHEN c.latency_ms < 30000 THEN '10-30s' WHEN c.latency_ms < 60000 THEN '30-60s' ELSE '>60s' END b, "
            f"COUNT(*) {FROM} WHERE {W} AND c.latency_ms IS NOT NULL GROUP BY b", p)
        order = ['<2s', '2-10s', '10-30s', '30-60s', '>60s']
        lmap = {r[0]: int(r[1]) for r in lat}
        ltot = sum(lmap.values()) or 1
        latency_hist = [{"bucket": b, "count": lmap.get(b, 0),
                         "pct": round(100.0 * lmap.get(b, 0) / ltot, 1)} for b in order]

        # --- per-widget / per-store ---
        by_embed = [{"embed_id": r[0], "name": r[1] or r[0], "store": r[2] or "(global)",
                     "requests": int(r[3] or 0), "errors": int(r[4] or 0),
                     "p95_ms": int(r[5] or 0), "last": str(r[6]) if r[6] else None}
                    for r in _rows(
            f"SELECT c.embed_id, MAX(e.name), MAX(e.bound_scope_id), COUNT(*), "
            f"COALESCE(SUM((NOT c.success)::int),0), "
            f"COALESCE(percentile_disc(0.95) WITHIN GROUP (ORDER BY c.latency_ms),0), MAX(c.ts) "
            f"{FROM} WHERE {W} GROUP BY c.embed_id ORDER BY 4 DESC LIMIT 50", p)]

        # --- top users ---
        top_users = [{"user": r[0], "requests": int(r[1] or 0)} for r in _rows(
            f"SELECT c.external_user, COUNT(*) {FROM} WHERE {W} AND c.external_user IS NOT NULL "
            f"GROUP BY c.external_user ORDER BY 2 DESC LIMIT 10", p)]

        # --- origins ---
        origins = [{"origin": r[0], "requests": int(r[1] or 0)} for r in _rows(
            f"SELECT COALESCE(NULLIF(c.origin,''),'(direct)'), COUNT(*) {FROM} WHERE {W} "
            f"GROUP BY 1 ORDER BY 2 DESC LIMIT 10", p)]

        return {"days": days, "bucket": bkt, "embed_id": embed_id, "kpi": kpi,
                "series": series, "latency_hist": latency_hist, "by_embed": by_embed,
                "top_users": top_users, "origins": origins}
    except Exception as e:
        logger.warning("GET /api/admin/usage/embed-overview failed: %s", e)
        return {"days": days, "bucket": bkt, "embed_id": embed_id,
                "kpi": {"requests": 0, "error_pct": 0.0, "latency_avg_ms": 0,
                        "latency_p50_ms": 0, "latency_p95_ms": 0, "latency_p99_ms": 0,
                        "uniq_users": 0, "uniq_sessions": 0, "active_embeds": "0/0",
                        "avg_resp_chars": 0},
                "series": [], "latency_hist": [], "by_embed": [], "top_users": [],
                "origins": [], "error": str(e)}


@router.get("/embed-detail")
def embed_detail(request: Request, embed_id: str, range: str = "7d"):
    """Single-widget drill-down screen (mirrors gateway outlet-detail).

    Returns header (name/store/scope/created), KPIs, bucketed activity series,
    latency histogram, errors block, top users/origins, and a per-CALL list for
    one embed_id. Chat bodies (question/answer text) are surfaced only if the
    optional message_text/response_text columns exist AND hold data
    (`messages_enabled`); embeds do not log bodies today, so it returns False and
    the calls list carries metadata only. No token/cost for embeds.
    """
    _gate(request)
    try:
        from dash.single_agent import locked_slug
        slug = locked_slug()
    except Exception:
        slug = "citypharma"
    # range -> window + bucket
    rg = (range or "7d").lower()
    days = {"24h": 1, "7d": 7, "30d": 30}.get(rg, 7)
    bkt = "hour" if days <= 1 else "day"
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=days)
    p: dict = {"s": start, "e": now, "slug": slug, "eid": embed_id}
    W = "c.ts >= :s AND c.ts < :e AND c.embed_id = :eid AND e.project_slug = :slug"
    FROM = ("FROM public.dash_embed_calls c "
            "JOIN public.dash_agent_embeds e ON e.embed_id = c.embed_id")

    # detect optional chat-body columns (future EMBED_LOG_BODIES); absent today
    try:
        body_cols = {r[0] for r in _rows(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='dash_embed_calls' "
            "AND column_name IN ('message_text','response_text')", {})}
    except Exception:
        body_cols = set()
    has_q = "message_text" in body_cols
    has_a = "response_text" in body_cols

    empty = {"embed_id": embed_id, "range": rg, "bucket": bkt,
             "header": {}, "messages_enabled": False,
             "kpi": {"requests": 0, "error_pct": 0.0, "latency_avg_ms": 0,
                     "latency_p50_ms": 0, "latency_p95_ms": 0, "latency_p99_ms": 0,
                     "uniq_users": 0, "uniq_sessions": 0, "avg_resp_chars": 0,
                     "avg_msg_chars": 0, "errors": 0},
             "series": [], "latency_hist": [], "errors": {"total": 0, "recent": []},
             "top_users": [], "origins": [], "calls": []}
    try:
        # --- header (embed meta) ---
        hr = _rows(
            "SELECT name, bound_scope_id, bound_intent, created_at, enabled "
            "FROM public.dash_agent_embeds WHERE embed_id = :eid AND project_slug = :slug",
            {"eid": embed_id, "slug": slug})
        if not hr:
            return empty
        h = hr[0]
        header = {"embed_id": embed_id, "name": h[0] or embed_id,
                  "store": h[1] or "(global)", "scope": h[2] or "private",
                  "created": str(h[3]) if h[3] else None, "enabled": bool(h[4])}

        # --- KPI ---
        k = _rows(
            f"SELECT COUNT(*), COALESCE(SUM(c.success::int),0), "
            f"COALESCE(AVG(c.latency_ms) FILTER (WHERE c.latency_ms IS NOT NULL),0), "
            f"COUNT(DISTINCT c.external_user) FILTER (WHERE c.external_user IS NOT NULL), "
            f"COUNT(DISTINCT c.session_token) FILTER (WHERE c.session_token IS NOT NULL), "
            f"COALESCE(AVG(c.response_chars) FILTER (WHERE c.response_chars IS NOT NULL),0), "
            f"COALESCE(AVG(c.message_chars) FILTER (WHERE c.message_chars IS NOT NULL),0) "
            f"{FROM} WHERE {W}", p)
        kr = k[0] if k else (0, 0, 0, 0, 0, 0, 0)
        reqs = int(kr[0] or 0)
        ok = int(kr[1] or 0)
        pct = _rows(
            f"SELECT percentile_disc(0.5) WITHIN GROUP (ORDER BY c.latency_ms), "
            f"percentile_disc(0.95) WITHIN GROUP (ORDER BY c.latency_ms), "
            f"percentile_disc(0.99) WITHIN GROUP (ORDER BY c.latency_ms) "
            f"{FROM} WHERE {W} AND c.latency_ms IS NOT NULL", p)
        pr = pct[0] if pct else (None, None, None)
        kpi = {
            "requests": reqs,
            "error_pct": round(100.0 * (1 - (ok / reqs)), 2) if reqs else 0.0,
            "latency_avg_ms": int(kr[2] or 0),
            "latency_p50_ms": int(pr[0] or 0),
            "latency_p95_ms": int(pr[1] or 0),
            "latency_p99_ms": int(pr[2] or 0),
            "uniq_users": int(kr[3] or 0),
            "uniq_sessions": int(kr[4] or 0),
            "avg_resp_chars": int(kr[5] or 0),
            "avg_msg_chars": int(kr[6] or 0),
            "errors": reqs - ok,
        }

        # --- activity series ---
        series = [{"t": str(r[0]), "requests": int(r[1] or 0), "errors": int(r[2] or 0),
                   "p95_ms": int(r[3] or 0)}
                  for r in _rows(
            f"SELECT date_trunc('{bkt}', c.ts) b, COUNT(*), "
            f"COALESCE(SUM((NOT c.success)::int),0), "
            f"COALESCE(percentile_disc(0.95) WITHIN GROUP (ORDER BY c.latency_ms),0) "
            f"{FROM} WHERE {W} GROUP BY b ORDER BY b", p)]

        # --- latency histogram ---
        lat = _rows(
            f"SELECT CASE WHEN c.latency_ms < 2000 THEN '<2s' WHEN c.latency_ms < 10000 THEN '2-10s' "
            f"WHEN c.latency_ms < 30000 THEN '10-30s' WHEN c.latency_ms < 60000 THEN '30-60s' ELSE '>60s' END b, "
            f"COUNT(*) {FROM} WHERE {W} AND c.latency_ms IS NOT NULL GROUP BY b", p)
        order = ['<2s', '2-10s', '10-30s', '30-60s', '>60s']
        lmap = {r[0]: int(r[1]) for r in lat}
        ltot = sum(lmap.values()) or 1
        latency_hist = [{"bucket": b, "count": lmap.get(b, 0),
                         "pct": round(100.0 * lmap.get(b, 0) / ltot, 1)} for b in order]

        # --- errors block (recent failures) ---
        err_recent = [{"ts": str(r[0]), "user": r[1] or "anon", "error": r[2] or "error"}
                      for r in _rows(
            f"SELECT c.ts, c.external_user, c.error {FROM} WHERE {W} AND c.success IS FALSE "
            f"ORDER BY c.ts DESC LIMIT 20", p)]
        errors = {"total": reqs - ok, "recent": err_recent}

        # --- top users / origins ---
        top_users = [{"user": r[0], "requests": int(r[1] or 0)} for r in _rows(
            f"SELECT c.external_user, COUNT(*) {FROM} WHERE {W} AND c.external_user IS NOT NULL "
            f"GROUP BY c.external_user ORDER BY 2 DESC LIMIT 10", p)]
        origins = [{"origin": r[0], "requests": int(r[1] or 0)} for r in _rows(
            f"SELECT COALESCE(NULLIF(c.origin,''),'(direct)'), COUNT(*) {FROM} WHERE {W} "
            f"GROUP BY 1 ORDER BY 2 DESC LIMIT 10", p)]

        # --- per-call list (metadata; bodies only if columns exist) ---
        qcol = "c.message_text" if has_q else "NULL"
        acol = "c.response_text" if has_a else "NULL"
        calls = [{"id": r[0], "ts": str(r[1]), "user": r[2] or "anon",
                  "session": r[3], "success": bool(r[4]), "error": r[5],
                  "msg_chars": int(r[6] or 0), "resp_chars": int(r[7] or 0),
                  "latency_ms": int(r[8] or 0) if r[8] is not None else None,
                  "origin": r[9] or "(direct)",
                  "question": r[10], "answer": r[11]}
                 for r in _rows(
            f"SELECT c.id, c.ts, c.external_user, c.session_token, c.success, c.error, "
            f"c.message_chars, c.response_chars, c.latency_ms, c.origin, {qcol}, {acol} "
            f"{FROM} WHERE {W} ORDER BY c.ts DESC LIMIT 100", p)]
        messages_enabled = has_q and any(c.get("question") for c in calls)

        return {"embed_id": embed_id, "range": rg, "bucket": bkt, "header": header,
                "messages_enabled": messages_enabled, "kpi": kpi, "series": series,
                "latency_hist": latency_hist, "errors": errors, "top_users": top_users,
                "origins": origins, "calls": calls}
    except Exception as e:
        logger.warning("GET /api/admin/usage/embed-detail failed: %s", e)
        empty["error"] = str(e)
        return empty


@router.get("/gateway-questions")
def gateway_questions(request: Request, range: str = "7d", key: str | None = None):
    _gate(request)
    start, end, _, _ = _range_window(range)
    try:
        filt = ["role='user'", "ts >= :s", "ts < :e"]
        p = {"s": start, "e": end}
        if key:
            filt.append("(service_account = :k OR service_account = 'svc:' || :k)")
            p["k"] = key
        rows = _rows("SELECT content FROM public.dash_apigw_messages WHERE " + " AND ".join(filt), p)
        enabled = len(rows) > 0
        import re
        buckets = {"stock check": 0, "substitutes": 0, "drug info / uses": 0, "analytics (SQL)": 0, "other": 0}
        for r in rows:
            q = (r[0] or "").lower()
            if re.search(r"substitut|alternativ|replace", q): buckets["substitutes"] += 1
            elif re.search(r"\b(use|uses|used for|indication|about|tell me more|side effect|dosage|dose|salt|composition|what is)\b", q): buckets["drug info / uses"] += 1
            elif re.search(r"\b(stock|in stock|available|how many|units|quantity|balance|count)\b", q): buckets["stock check"] += 1
            elif re.search(r"\b(total|sum|average|breakdown|category|compare|trend|top|sql|analy)\b", q): buckets["analytics (SQL)"] += 1
            else: buckets["other"] += 1
        total = sum(buckets.values()) or 1
        intents = [{"label": k, "count": v, "pct": round(100.0 * v / total, 1)} for k, v in sorted(buckets.items(), key=lambda x: -x[1]) if v > 0]
        return {"intents": intents, "total": sum(buckets.values()), "messages_enabled": enabled}
    except Exception as e:
        return {"intents": [], "total": 0, "messages_enabled": False, "error": str(e)}


@router.get("/gateway-tools")
def gateway_tools(request: Request, range: str = "7d"):
    _gate(request)
    start, end, _, _ = _range_window(range)
    try:
        rows = _rows("SELECT COALESCE(meta->>'tool', split_part(name,'.',3)) t, COUNT(*) "
                     "FROM public.dash_traces WHERE name LIKE 'chat.analyst.%' AND started_at >= :s AND started_at < :e "
                     "GROUP BY 1 ORDER BY 2 DESC LIMIT 20", {"s": start, "e": end})
        tools = [{"tool": r[0] or "(tool)", "count": int(r[1])} for r in rows if r[0]]
        return {"tools": tools, "total": sum(t["count"] for t in tools)}
    except Exception as e:
        return {"tools": [], "total": 0, "error": str(e)}
