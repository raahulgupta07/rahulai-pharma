"""Telemetry admin API — super-admin observability endpoints.

Reads from dash.tel_* tables (migration 122) plus live traces. All endpoints
are super-admin gated and fail-soft: any DB hiccup returns an empty/default
payload with an `error` string rather than 500.

Mirrors the style of app/traces_api.py and app/admin_connectors.py.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import text

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/telemetry-admin", tags=["telemetry-admin"])


# ---------------------------------------------------------------------------
# Auth helpers (mirror admin_connectors / traces_api)
# ---------------------------------------------------------------------------
def _get_user(request: Request) -> dict:
    user = getattr(getattr(request, "state", None), "user", None)
    if not user:
        raise HTTPException(401, "Not authenticated")
    return user


def _require_super(user: dict):
    if not user.get("is_super_admin") and not user.get("is_super"):
        raise HTTPException(403, "super-admin only")


def _gate(request: Request):
    _require_super(_get_user(request))


def _iso(v):
    if v is None:
        return None
    try:
        return v.isoformat()
    except AttributeError:
        return str(v)


def _engine():
    from db import get_sql_engine
    return get_sql_engine()


def _safe_exec(query: str, params: dict | None = None) -> list:
    """Run a SELECT and return rows. Empty list on any error."""
    try:
        eng = _engine()
        with eng.connect() as conn:
            return conn.execute(text(query), params or {}).fetchall()
    except Exception as e:
        logger.debug("telemetry safe_exec failed: %s", e)
        return []


def _safe_exec_one(query: str, params: dict | None = None):
    rows = _safe_exec(query, params)
    return rows[0] if rows else None


# ---------------------------------------------------------------------------
# /live — real-time activity strip
# ---------------------------------------------------------------------------
@router.get("/live")
def live(request: Request):
    _gate(request)
    empty = {
        "tokens_per_min": 0,
        "active_runs": 0,
        "queue_len": 0,
        "workers_busy": 0,
        "workers_total": 8,
        "recent_calls": [],
    }
    try:
        # Recent 20 trace rows (any kind).
        rows = _safe_exec(
            "SELECT started_at, name, kind, duration_ms, status "
            "FROM public.dash_traces "
            "ORDER BY started_at DESC NULLS LAST LIMIT 20"
        )
        recent = []
        for r in rows:
            name = r[1] or ""
            parts = str(name).split(".")
            agent = parts[1] if len(parts) >= 2 and parts[1] else (parts[0] or "—")
            tool = parts[-1] if len(parts) >= 3 else (parts[1] if len(parts) >= 2 else name)
            recent.append({
                "ts": _iso(r[0]),
                "agent": agent,
                "tool": tool,
                "dur_ms": int(r[3]) if r[3] is not None else None,
                "status": r[4] or "unknown",
            })

        # tokens/min and active runs from traces in last 60s.
        tpm_row = _safe_exec_one(
            "SELECT COALESCE(SUM(tokens), 0) AS tok, COUNT(*) FILTER (WHERE status='running') AS active "
            "FROM public.dash_traces WHERE started_at >= now() - interval '60 seconds'"
        )
        tokens_per_min = int(tpm_row[0]) if tpm_row and tpm_row[0] is not None else 0
        active_runs = int(tpm_row[1]) if tpm_row and tpm_row[1] is not None else 0

        # Queue length — best-effort from a generic queue table if present.
        ql_row = _safe_exec_one(
            "SELECT COUNT(*) FROM public.dash_minions WHERE status IN ('queued','pending')"
        )
        queue_len = int(ql_row[0]) if ql_row else 0

        return {
            "tokens_per_min": tokens_per_min,
            "active_runs": active_runs,
            "queue_len": queue_len,
            "workers_busy": min(active_runs, 8),
            "workers_total": 8,
            "recent_calls": recent,
        }
    except Exception as e:
        logger.warning("/live failed: %s", e)
        out = dict(empty)
        out["error"] = str(e)
        return out


# ---------------------------------------------------------------------------
# /cost — daily + MTD totals + by_model/agent/project + sparkline
# ---------------------------------------------------------------------------
@router.get("/cost")
def cost(request: Request):
    _gate(request)
    empty = {
        "hour_rate": 0.0,
        "today_total": 0.0,
        "mtd_total": 0.0,
        "budget_pct": 0.0,
        "by_model": [],
        "by_agent": [],
        "by_project": [],
        "sparkline_7d": [],
    }
    try:
        rows = _safe_exec(
            "SELECT day, cost_usd FROM dash.tel_cost_daily "
            "WHERE day >= CURRENT_DATE - 6 ORDER BY day ASC"
        )
        spark = [float(r[1] or 0) for r in rows]
        today_total = spark[-1] if spark else 0.0
        # MTD from tel_cost_daily this month.
        mtd_row = _safe_exec_one(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM dash.tel_cost_daily "
            "WHERE day >= date_trunc('month', CURRENT_DATE)"
        )
        mtd_total = float(mtd_row[0]) if mtd_row else 0.0

        # Hour rate = today_total / hours_elapsed (best-effort).
        hour_rate_row = _safe_exec_one(
            "SELECT GREATEST(EXTRACT(hour FROM now())::int, 1)"
        )
        hours_elapsed = int(hour_rate_row[0]) if hour_rate_row else 1
        hour_rate = round(today_total / hours_elapsed, 4) if hours_elapsed else 0.0

        budget = 1000.0  # placeholder monthly budget
        budget_pct = round((mtd_total / budget) * 100, 2) if budget else 0.0

        # by_model / by_agent / by_project from traces (last 24h).
        def _share(query: str, key: str) -> list:
            try:
                eng = _engine()
                with eng.connect() as conn:
                    raw = conn.execute(text(query)).fetchall()
                total = sum(float(r[1] or 0) for r in raw) or 1.0
                return [
                    {"name": r[0] or "—",
                     "share_pct": round((float(r[1] or 0) / total) * 100, 2)}
                    for r in raw[:10]
                ]
            except Exception:
                return []

        by_model = _share(
            "SELECT COALESCE(meta->>'model', 'unknown') AS m, "
            "COALESCE(SUM(cost_usd), 0) AS c "
            "FROM public.dash_traces "
            "WHERE started_at >= now() - interval '24 hours' "
            "GROUP BY m ORDER BY c DESC", "model")
        by_agent = _share(
            "SELECT split_part(name, '.', 2) AS a, COALESCE(SUM(cost_usd), 0) AS c "
            "FROM public.dash_traces "
            "WHERE started_at >= now() - interval '24 hours' "
            "GROUP BY a ORDER BY c DESC", "agent")
        by_project = _share(
            "SELECT COALESCE(project_slug, '—') AS p, COALESCE(SUM(cost_usd), 0) AS c "
            "FROM public.dash_traces "
            "WHERE started_at >= now() - interval '24 hours' "
            "GROUP BY p ORDER BY c DESC", "project")

        return {
            "hour_rate": hour_rate,
            "today_total": round(today_total, 4),
            "mtd_total": round(mtd_total, 4),
            "budget_pct": budget_pct,
            "by_model": by_model,
            "by_agent": by_agent,
            "by_project": by_project,
            "sparkline_7d": spark,
        }
    except Exception as e:
        logger.warning("/cost failed: %s", e)
        out = dict(empty)
        out["error"] = str(e)
        return out


# ---------------------------------------------------------------------------
# /errors — last 24h error breakdown
# ---------------------------------------------------------------------------
@router.get("/errors")
def errors(request: Request):
    _gate(request)
    empty = {
        "error_rate_pct": 0.0,
        "slo_target_pct": 1.0,
        "breakdown": [],
    }
    try:
        agg = _safe_exec_one(
            "SELECT COUNT(*) AS total, "
            "COUNT(*) FILTER (WHERE status='error') AS errs "
            "FROM public.dash_traces "
            "WHERE started_at >= now() - interval '24 hours'"
        )
        total = int(agg[0]) if agg else 0
        errs = int(agg[1]) if agg else 0
        rate = round((errs / total) * 100, 3) if total else 0.0

        rows = _safe_exec(
            "SELECT COALESCE(error, 'unknown') AS code, COUNT(*) AS cnt, "
            "(array_agg(split_part(name, '.', 2)))[1] AS top_tool, "
            "(array_agg(error))[1] AS sample "
            "FROM public.dash_traces "
            "WHERE status='error' AND started_at >= now() - interval '24 hours' "
            "GROUP BY code ORDER BY cnt DESC LIMIT 20"
        )
        breakdown = [
            {
                "code": (r[0] or "unknown")[:80],
                "count_24h": int(r[1] or 0),
                "top_tool": r[2] or "—",
                "sample": (r[3] or "")[:240],
            }
            for r in rows
        ]
        return {
            "error_rate_pct": rate,
            "slo_target_pct": 1.0,
            "breakdown": breakdown,
        }
    except Exception as e:
        logger.warning("/errors failed: %s", e)
        out = dict(empty)
        out["error"] = str(e)
        return out


# ---------------------------------------------------------------------------
# /latency — global p50/p95/p99 + per-tool
# ---------------------------------------------------------------------------
@router.get("/latency")
def latency(request: Request):
    _gate(request)
    empty = {"p50": 0, "p95": 0, "p99": 0, "slo_pct": 95.0, "by_tool": []}
    try:
        rows = _safe_exec(
            "SELECT tool_name, p50_ms, p95_ms, p99_ms, err_pct "
            "FROM dash.tel_tool_stats ORDER BY p95_ms DESC"
        )
        by_tool = []
        p50s, p95s, p99s = [], [], []
        for r in rows:
            p50 = int(r[1] or 0)
            p95 = int(r[2] or 0)
            p99 = int(r[3] or 0)
            err = float(r[4] or 0)
            p50s.append(p50); p95s.append(p95); p99s.append(p99)
            if p95 > 1000 or err > 2.0:
                state = "breach"
            elif p95 > 700 or err > 1.0:
                state = "warn"
            else:
                state = "ok"
            by_tool.append({
                "tool": r[0],
                "p50": p50, "p95": p95, "p99": p99,
                "err_pct": err,
                "slo_state": state,
            })

        def _avg(xs):
            return int(sum(xs) / len(xs)) if xs else 0

        return {
            "p50": _avg(p50s),
            "p95": _avg(p95s),
            "p99": _avg(p99s),
            "slo_pct": 95.0,
            "by_tool": by_tool,
        }
    except Exception as e:
        logger.warning("/latency failed: %s", e)
        out = dict(empty)
        out["error"] = str(e)
        return out


# ---------------------------------------------------------------------------
# /tool-usage — ranked top tools by call volume
# ---------------------------------------------------------------------------
@router.get("/tool-usage")
def tool_usage(request: Request):
    _gate(request)
    empty = {"total_tools": 0, "top10_share_pct": 0.0, "ranked": []}
    try:
        rows = _safe_exec(
            "SELECT tool_name, calls_24h FROM dash.tel_tool_stats "
            "ORDER BY calls_24h DESC"
        )
        total_calls = sum(int(r[1] or 0) for r in rows) or 1
        ranked = []
        for i, r in enumerate(rows, start=1):
            calls = int(r[1] or 0)
            ranked.append({
                "rank": i,
                "tool": r[0],
                "calls": calls,
                "share_pct": round((calls / total_calls) * 100, 2),
            })
        top10 = sum(item["calls"] for item in ranked[:10])
        top10_share = round((top10 / total_calls) * 100, 2) if total_calls else 0.0
        return {
            "total_tools": len(rows),
            "top10_share_pct": top10_share,
            "ranked": ranked,
        }
    except Exception as e:
        logger.warning("/tool-usage failed: %s", e)
        out = dict(empty)
        out["error"] = str(e)
        return out


# ---------------------------------------------------------------------------
# /connector-health — from tel_connector_health, fallback to dash_connections
# ---------------------------------------------------------------------------
@router.get("/connector-health")
def connector_health(request: Request):
    _gate(request)
    try:
        rows = _safe_exec(
            "SELECT conn_name, conn_type, last_test_at, p95_ms, err_pct "
            "FROM dash.tel_connector_health ORDER BY conn_name ASC"
        )
        if rows:
            return {
                "connectors": [
                    {
                        "name": r[0],
                        "type": r[1],
                        "last_test_at": _iso(r[2]),
                        "p95_ms": int(r[3] or 0),
                        "err_pct": float(r[4] or 0),
                    }
                    for r in rows
                ]
            }
        # Fallback: derive shells from dash.dash_connections (no health data).
        fb = _safe_exec(
            "SELECT name, kind, last_tested_at FROM dash.dash_connections "
            "ORDER BY name ASC LIMIT 50"
        )
        return {
            "connectors": [
                {
                    "name": r[0],
                    "type": r[1],
                    "last_test_at": _iso(r[2]),
                    "p95_ms": 0,
                    "err_pct": 0.0,
                }
                for r in fb
            ]
        }
    except Exception as e:
        logger.warning("/connector-health failed: %s", e)
        return {"connectors": [], "error": str(e)}


# ---------------------------------------------------------------------------
# /token-flow — in/out + cache stats + sankey
# ---------------------------------------------------------------------------
@router.get("/token-flow")
def token_flow(request: Request):
    _gate(request)
    empty = {
        "tokens_in_24h": 0,
        "tokens_out_24h": 0,
        "ratio": 0.0,
        "cache_hit_pct": 0.0,
        "est_savings_per_day": 0.0,
        "sankey_nodes": [],
        "sankey_links": [],
    }
    try:
        # Today's row from tel_cost_daily.
        today = _safe_exec_one(
            "SELECT tokens_in, tokens_out, cache_hits_pct, cost_usd "
            "FROM dash.tel_cost_daily WHERE day = CURRENT_DATE"
        )
        if today:
            tin = int(today[0] or 0)
            tout = int(today[1] or 0)
            cache_pct = float(today[2] or 0)
            cost_today = float(today[3] or 0)
        else:
            # Fallback to traces sum.
            r = _safe_exec_one(
                "SELECT COALESCE(SUM(tokens), 0) FROM public.dash_traces "
                "WHERE started_at >= now() - interval '24 hours'"
            )
            tin = int(r[0]) if r else 0
            tout = int(tin * 0.35)
            cache_pct = 0.0
            cost_today = 0.0

        ratio = round(tout / tin, 3) if tin else 0.0
        # Naive savings estimate: cache_pct of cost would have been billed.
        est_savings = round((cache_pct / 100.0) * cost_today, 4)

        sankey_nodes = [
            {"name": "Input"},
            {"name": "Cache"},
            {"name": "LLM"},
            {"name": "Output"},
        ]
        cache_tokens = int(tin * (cache_pct / 100.0))
        llm_tokens = tin - cache_tokens
        sankey_links = [
            {"source": "Input",  "target": "Cache", "value": cache_tokens},
            {"source": "Input",  "target": "LLM",   "value": llm_tokens},
            {"source": "Cache",  "target": "Output", "value": cache_tokens},
            {"source": "LLM",    "target": "Output", "value": tout},
        ]

        return {
            "tokens_in_24h": tin,
            "tokens_out_24h": tout,
            "ratio": ratio,
            "cache_hit_pct": cache_pct,
            "est_savings_per_day": est_savings,
            "sankey_nodes": sankey_nodes,
            "sankey_links": sankey_links,
        }
    except Exception as e:
        logger.warning("/token-flow failed: %s", e)
        out = dict(empty)
        out["error"] = str(e)
        return out


# ---------------------------------------------------------------------------
# /alerts — list + silence/unsilence
# ---------------------------------------------------------------------------
@router.get("/alerts")
def list_alerts(request: Request, firing: bool = True):
    _gate(request)
    try:
        if firing:
            rows = _safe_exec(
                "SELECT id, severity, rule_name, triggered_at, silenced, owner, detail "
                "FROM dash.tel_alerts WHERE silenced = FALSE "
                "ORDER BY triggered_at DESC LIMIT 200"
            )
        else:
            rows = _safe_exec(
                "SELECT id, severity, rule_name, triggered_at, silenced, owner, detail "
                "FROM dash.tel_alerts ORDER BY triggered_at DESC LIMIT 200"
            )
        return {
            "alerts": [
                {
                    "id": int(r[0]),
                    "severity": r[1],
                    "rule_name": r[2],
                    "triggered_at": _iso(r[3]),
                    "silenced": bool(r[4]),
                    "owner": r[5],
                    "detail": r[6],
                }
                for r in rows
            ]
        }
    except Exception as e:
        logger.warning("/alerts failed: %s", e)
        return {"alerts": [], "error": str(e)}


def _set_silenced(alert_id: int, silenced: bool) -> dict:
    try:
        eng = _engine()
        with eng.begin() as conn:
            res = conn.execute(
                text("UPDATE dash.tel_alerts SET silenced = :s WHERE id = :i"),
                {"s": silenced, "i": alert_id},
            )
            if res.rowcount == 0:
                raise HTTPException(404, f"alert {alert_id} not found")
        return {"ok": True, "id": alert_id, "silenced": silenced}
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("alert silence toggle failed: %s", e)
        return {"ok": False, "id": alert_id, "error": str(e)}


@router.post("/alerts/{alert_id}/silence")
def silence_alert(alert_id: int, request: Request):
    _gate(request)
    return _set_silenced(alert_id, True)


@router.post("/alerts/{alert_id}/unsilence")
def unsilence_alert(alert_id: int, request: Request):
    _gate(request)
    return _set_silenced(alert_id, False)


# ---------------------------------------------------------------------------
# /summary — headline numbers for the top strip
# ---------------------------------------------------------------------------
@router.get("/summary")
def summary(request: Request):
    _gate(request)
    empty = {
        "today_cost_usd": 0.0,
        "mtd_cost_usd": 0.0,
        "error_rate_pct": 0.0,
        "p95_ms": 0,
        "active_alerts": 0,
        "tokens_24h": 0,
        "tools_tracked": 0,
        "connectors_tracked": 0,
    }
    try:
        cost_row = _safe_exec_one(
            "SELECT COALESCE(SUM(cost_usd) FILTER (WHERE day = CURRENT_DATE), 0), "
            "       COALESCE(SUM(cost_usd) FILTER (WHERE day >= date_trunc('month', CURRENT_DATE)), 0), "
            "       COALESCE(SUM(tokens_in + tokens_out) FILTER (WHERE day = CURRENT_DATE), 0) "
            "FROM dash.tel_cost_daily"
        )
        today_cost = float(cost_row[0]) if cost_row else 0.0
        mtd_cost = float(cost_row[1]) if cost_row else 0.0
        tokens_24h = int(cost_row[2]) if cost_row else 0

        err_row = _safe_exec_one(
            "SELECT COUNT(*), COUNT(*) FILTER (WHERE status='error') "
            "FROM public.dash_traces WHERE started_at >= now() - interval '24 hours'"
        )
        total = int(err_row[0]) if err_row else 0
        errs = int(err_row[1]) if err_row else 0
        err_rate = round((errs / total) * 100, 3) if total else 0.0

        p95_row = _safe_exec_one(
            "SELECT COALESCE(AVG(p95_ms), 0)::int FROM dash.tel_tool_stats"
        )
        p95 = int(p95_row[0]) if p95_row else 0

        alerts_row = _safe_exec_one(
            "SELECT COUNT(*) FROM dash.tel_alerts WHERE silenced = FALSE"
        )
        active_alerts = int(alerts_row[0]) if alerts_row else 0

        tools_row = _safe_exec_one("SELECT COUNT(*) FROM dash.tel_tool_stats")
        conns_row = _safe_exec_one("SELECT COUNT(*) FROM dash.tel_connector_health")

        return {
            "today_cost_usd": round(today_cost, 4),
            "mtd_cost_usd": round(mtd_cost, 4),
            "error_rate_pct": err_rate,
            "p95_ms": p95,
            "active_alerts": active_alerts,
            "tokens_24h": tokens_24h,
            "tools_tracked": int(tools_row[0]) if tools_row else 0,
            "connectors_tracked": int(conns_row[0]) if conns_row else 0,
        }
    except Exception as e:
        logger.warning("/summary failed: %s", e)
        out = dict(empty)
        out["error"] = str(e)
        return out
