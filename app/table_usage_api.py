"""Per-table usage telemetry — aggregates dash_traces into stats per FQN.

Mirrors the super-admin pattern in app/admin_connectors.py. The /refresh
endpoint (also called by the hourly cron) parses dash_traces.meta->>'sql'
via sqlglot, extracts table references, groups them, and writes into
public.dash_table_usage_stats + REFRESH MATERIALIZED VIEW mv_table_usage.

All read endpoints fail-soft (empty list + error string) so a missing MV
or stats table never 500s.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/table-usage", tags=["table-usage"])


# ── Auth helpers (mirror admin_connectors.py) ─────────────────────────────
def _get_user(request: Request) -> dict:
    user = getattr(getattr(request, "state", None), "user", None)
    if not user:
        raise HTTPException(401, "Not authenticated")
    return user


def _require_super(user: dict):
    if not user.get("is_admin"):
        raise HTTPException(403, "super-admin only")


def _engine():
    """Read-side engine (queries, reads)."""
    try:
        from db.session import get_sql_engine
        return get_sql_engine()
    except Exception:
        from db import get_sql_engine  # type: ignore
        return get_sql_engine()


def _write_engine():
    """Write-side engine for public.dash_* tables (writes are blocked on the
    read engine — see CLAUDE.md "platform-metadata writes")."""
    try:
        from db.session import get_write_engine
        return get_write_engine()
    except Exception:
        # Fallback: use the read engine if write engine isn't exposed.
        return _engine()


def _iso(v):
    if v is None:
        return None
    try:
        return v.isoformat()
    except AttributeError:
        return str(v)


# ── Refresh: parse traces → stats → MV ────────────────────────────────────
def refresh_table_usage() -> dict[str, Any]:
    """Parse last 30d of dash_traces SQL, populate stats table, refresh MV.

    Returns counts dict. Fail-soft on per-row parse errors.
    """
    from sqlalchemy import text as _t

    out = {"traces_scanned": 0, "tables_extracted": 0, "rows_written": 0, "errors": 0}

    try:
        import sqlglot
        from sqlglot import exp
    except Exception as e:
        logger.warning("table_usage refresh: sqlglot unavailable: %s", e)
        out["errors"] = 1
        out["error"] = "sqlglot unavailable"
        return out

    eng = _engine()
    if eng is None:
        out["errors"] = 1
        out["error"] = "no db engine"
        return out

    # Pull traces with SQL in meta. dash_traces stores meta as JSONB.
    # Keep the query simple — pull last 30d once and bucket in Python.
    try:
        with eng.connect() as conn:
            rows = conn.execute(_t(
                "SELECT started_at, duration_ms, status, project_slug, "
                "       meta->>'sql' AS sql, meta->>'user_id' AS uid "
                "FROM public.dash_traces "
                "WHERE started_at >= now() - interval '30 days' "
                "  AND meta ? 'sql' "
                "  AND meta->>'sql' <> ''"
            )).fetchall()
    except Exception as e:
        logger.warning("table_usage refresh: trace read failed: %s", e)
        out["errors"] = 1
        out["error"] = str(e)
        return out

    out["traces_scanned"] = len(rows)
    if not rows:
        # Still clear stats + refresh MV so stale data rolls out.
        try:
            with _write_engine().begin() as conn:
                conn.execute(_t("TRUNCATE public.dash_table_usage_stats"))
                conn.execute(_t("SELECT public.refresh_mv_table_usage()"))
        except Exception as e:
            logger.warning("table_usage refresh: empty MV refresh failed: %s", e)
        return out

    from collections import defaultdict
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    cutoff_7d = now - timedelta(days=7)

    # per-fqn buckets
    stats: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "q7": 0, "q30": 0, "last": None,
        "users": set(), "latencies": [], "errors": 0, "total": 0,
    })

    for r in rows:
        started_at, duration_ms, status, project_slug, sql, uid = r
        if not sql:
            continue
        try:
            parsed = sqlglot.parse(sql, read="postgres", error_level=None)
        except Exception:
            out["errors"] += 1
            continue

        seen_in_query: set[str] = set()
        for stmt in parsed:
            if stmt is None:
                continue
            for tbl in stmt.find_all(exp.Table):
                name = tbl.name
                if not name:
                    continue
                schema = tbl.db or "public"
                # If project_slug looks schema-ish and table has no explicit
                # schema, fall back to project_slug for FQN. Otherwise public.
                if not tbl.db and project_slug:
                    schema = project_slug
                fqn = f"{schema}.{name}".lower()
                if fqn in seen_in_query:
                    continue
                seen_in_query.add(fqn)

                s = stats[fqn]
                s["q30"] += 1
                if started_at and started_at >= cutoff_7d:
                    s["q7"] += 1
                if started_at and (s["last"] is None or started_at > s["last"]):
                    s["last"] = started_at
                if uid:
                    s["users"].add(uid)
                if duration_ms is not None:
                    try:
                        s["latencies"].append(float(duration_ms))
                    except (TypeError, ValueError):
                        pass
                s["total"] += 1
                if status == "error":
                    s["errors"] += 1

        out["tables_extracted"] += len(seen_in_query)

    # Write stats table (TRUNCATE + INSERT keeps it simple + atomic per refresh)
    try:
        with _write_engine().begin() as conn:
            conn.execute(_t("TRUNCATE public.dash_table_usage_stats"))
            insert_sql = _t(
                "INSERT INTO public.dash_table_usage_stats "
                "(table_fqn, query_count_7d, query_count_30d, last_used_at, "
                " distinct_users, avg_latency_ms, error_rate, refreshed_at) "
                "VALUES (:fqn, :q7, :q30, :last, :du, :avg, :err, now())"
            )
            for fqn, s in stats.items():
                avg_lat = (sum(s["latencies"]) / len(s["latencies"])) if s["latencies"] else None
                err_rate = (s["errors"] / s["total"]) if s["total"] else 0.0
                conn.execute(insert_sql, {
                    "fqn": fqn,
                    "q7": s["q7"],
                    "q30": s["q30"],
                    "last": s["last"],
                    "du": len(s["users"]),
                    "avg": avg_lat,
                    "err": round(err_rate, 4),
                })
                out["rows_written"] += 1

            # Refresh the MV from the stats table.
            conn.execute(_t("SELECT public.refresh_mv_table_usage()"))
    except Exception as e:
        logger.warning("table_usage refresh: write failed: %s", e)
        out["errors"] += 1
        out["error"] = str(e)

    return out


# ── Endpoints ─────────────────────────────────────────────────────────────
@router.get("/hot")
def hot_tables(request: Request, limit: int = 20):
    """Top N tables by query_count_30d. Auth: any logged-in user."""
    _get_user(request)
    limit_i = max(1, min(int(limit or 20), 200))

    from sqlalchemy import text as _t
    eng = _engine()
    try:
        with eng.connect() as conn:
            rows = conn.execute(_t(
                "SELECT table_fqn, query_count_7d, query_count_30d, "
                "       last_used_at, distinct_users, avg_latency_ms, error_rate "
                "FROM public.mv_table_usage "
                "ORDER BY query_count_30d DESC, last_used_at DESC NULLS LAST "
                "LIMIT :lim"
            ), {"lim": limit_i}).fetchall()
        return {"tables": [_row(r) for r in rows]}
    except Exception as e:
        logger.warning("table-usage/hot failed: %s", e)
        return {"tables": [], "error": str(e)}


@router.get("/cold")
def cold_tables(request: Request, limit: int = 20):
    """Least-used tables (query_count_30d ASC), excluding never-used."""
    _get_user(request)
    limit_i = max(1, min(int(limit or 20), 200))

    from sqlalchemy import text as _t
    eng = _engine()
    try:
        with eng.connect() as conn:
            rows = conn.execute(_t(
                "SELECT table_fqn, query_count_7d, query_count_30d, "
                "       last_used_at, distinct_users, avg_latency_ms, error_rate "
                "FROM public.mv_table_usage "
                "WHERE query_count_30d > 0 "
                "ORDER BY query_count_30d ASC, last_used_at ASC NULLS FIRST "
                "LIMIT :lim"
            ), {"lim": limit_i}).fetchall()
        return {"tables": [_row(r) for r in rows]}
    except Exception as e:
        logger.warning("table-usage/cold failed: %s", e)
        return {"tables": [], "error": str(e)}


@router.get("/table/{fqn:path}")
def table_detail(request: Request, fqn: str):
    """Single-table stats (case-insensitive)."""
    _get_user(request)
    from sqlalchemy import text as _t
    eng = _engine()
    try:
        with eng.connect() as conn:
            r = conn.execute(_t(
                "SELECT table_fqn, query_count_7d, query_count_30d, "
                "       last_used_at, distinct_users, avg_latency_ms, error_rate "
                "FROM public.mv_table_usage "
                "WHERE table_fqn = lower(:fqn)"
            ), {"fqn": fqn}).fetchone()
        if not r:
            return {"table": None}
        return {"table": _row(r)}
    except Exception as e:
        logger.warning("table-usage/table failed: %s", e)
        return {"table": None, "error": str(e)}


@router.post("/refresh")
def refresh_endpoint(request: Request):
    """Trigger a refresh cycle. Super-admin only."""
    user = _get_user(request)
    _require_super(user)
    return refresh_table_usage()


def _row(r) -> dict:
    return {
        "table_fqn": r[0],
        "query_count_7d": int(r[1] or 0),
        "query_count_30d": int(r[2] or 0),
        "last_used_at": _iso(r[3]),
        "distinct_users": int(r[4] or 0),
        "avg_latency_ms": float(r[5]) if r[5] is not None else None,
        "error_rate": float(r[6]) if r[6] is not None else None,
    }
