"""Admin observability API — agent/cron/learning execution traces.

Reads from public.dash_traces (created by a sibling migration). All endpoints
are super-admin gated and fail-soft: if the table is missing or a query errors,
an empty result + an `error` string is returned so the admin page never 500s.
"""
import logging
from fastapi import APIRouter, HTTPException, Request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["Traces"])


# Same auth helpers other admin endpoints use (mirrors app/admin_api.py).
def _get_user(request: Request) -> dict:
    user = getattr(getattr(request, "state", None), "user", None)
    if not user:
        raise HTTPException(401, "Not authenticated")
    return user


def _require_super(user: dict):
    if not user.get("is_super_admin"):
        raise HTTPException(403, "super-admin only")


def _clamp(val, default, lo, hi):
    try:
        v = int(val)
    except (TypeError, ValueError):
        return default
    return max(lo, min(v, hi))


def _agent_of(name: str) -> str:
    """Derive the agent segment of a dotted trace name.

    "chat.analyst.run_sql" -> "analyst" (index 1 when present).
    Falls back to the first segment, then the whole name.
    """
    if not name:
        return "—"
    parts = str(name).split(".")
    if len(parts) >= 2 and parts[1].strip():
        return parts[1].strip()
    return parts[0].strip() or "—"


def _iso(v):
    if v is None:
        return None
    try:
        return v.isoformat()
    except AttributeError:
        return str(v)


@router.get("/traces")
def list_traces(
    request: Request,
    kind: str = "",
    project: str = "",
    days: int = 1,
    limit: int = 200,
):
    """Recent ROOT traces (parent_id IS NULL), newest first, children nested.

    Filters: kind, project (project_slug), days lookback. One query for roots,
    one for their children, grouped in Python (no N+1).
    """
    user = _get_user(request)
    _require_super(user)

    days_i = _clamp(days, 1, 1, 90)
    limit_i = _clamp(limit, 200, 1, 1000)

    empty = {
        "traces": [],
        "rollup": {
            "runs": 0,
            "failed": 0,
            "cost_usd": 0.0,
            "by_kind": {},
            "slowest": {},
        },
    }

    from sqlalchemy import text
    from db import get_sql_engine

    eng = get_sql_engine()
    try:
        params = {"d": days_i, "lim": limit_i}
        where = ["parent_id IS NULL", "started_at >= now() - make_interval(days => :d)"]
        if kind:
            where.append("kind = :kind")
            params["kind"] = kind
        if project:
            where.append("project_slug = :proj")
            params["proj"] = project
        where_sql = " AND ".join(where)

        with eng.connect() as conn:
            roots = conn.execute(
                text(
                    "SELECT trace_id, name, kind, project_slug, status, "
                    "duration_ms, cost_usd, tokens, started_at, error "
                    f"FROM public.dash_traces WHERE {where_sql} "
                    "ORDER BY started_at DESC LIMIT :lim"
                ),
                params,
            ).fetchall()

            root_list = [
                {
                    "trace_id": r[0],
                    "name": r[1],
                    "kind": r[2],
                    "project_slug": r[3],
                    "status": r[4],
                    "duration_ms": int(r[5]) if r[5] is not None else None,
                    "cost_usd": float(r[6] or 0),
                    "tokens": int(r[7]) if r[7] is not None else None,
                    "started_at": _iso(r[8]),
                    "error": r[9],
                    "children": [],
                }
                for r in roots
            ]

            children_by_parent: dict = {}
            trace_ids = [t["trace_id"] for t in root_list]
            if trace_ids:
                child_rows = conn.execute(
                    text(
                        "SELECT parent_id, trace_id, name, kind, project_slug, status, "
                        "duration_ms, cost_usd, tokens, started_at, error "
                        "FROM public.dash_traces "
                        "WHERE parent_id = ANY(:ids) "
                        "ORDER BY started_at ASC"
                    ),
                    {"ids": trace_ids},
                ).fetchall()
                for c in child_rows:
                    children_by_parent.setdefault(c[0], []).append(
                        {
                            "trace_id": c[1],
                            "name": c[2],
                            "kind": c[3],
                            "project_slug": c[4],
                            "status": c[5],
                            "duration_ms": int(c[6]) if c[6] is not None else None,
                            "cost_usd": float(c[7] or 0),
                            "tokens": int(c[8]) if c[8] is not None else None,
                            "started_at": _iso(c[9]),
                            "error": c[10],
                        }
                    )

            for t in root_list:
                t["children"] = children_by_parent.get(t["trace_id"], [])

        # Rollup over root traces.
        runs = len(root_list)
        failed = sum(1 for t in root_list if t["status"] == "error")
        total_cost = round(sum(t["cost_usd"] for t in root_list), 6)
        by_kind: dict = {}
        slowest = {}
        slowest_ms = -1
        for t in root_list:
            by_kind[t["kind"] or "—"] = by_kind.get(t["kind"] or "—", 0) + 1
            dm = t["duration_ms"] or 0
            if dm > slowest_ms:
                slowest_ms = dm
                slowest = {
                    "trace_id": t["trace_id"],
                    "name": t["name"],
                    "kind": t["kind"],
                    "duration_ms": t["duration_ms"],
                }

        return {
            "traces": root_list,
            "rollup": {
                "runs": runs,
                "failed": failed,
                "cost_usd": total_cost,
                "by_kind": by_kind,
                "slowest": slowest,
            },
        }
    except Exception as e:  # missing table / DB hiccup → fail-soft empty
        logger.warning("traces query failed: %s", e)
        out = dict(empty)
        out["error"] = str(e)
        return out


@router.get("/traces/cron-health")
def cron_health(request: Request):
    """For each distinct kind='cron' name: last finished_at + status + stale flag.

    `stale` is True when the last run finished more than 26h ago (or never
    finished). Fail-soft to empty list.
    """
    user = _get_user(request)
    _require_super(user)

    from sqlalchemy import text
    from db import get_sql_engine

    eng = get_sql_engine()
    try:
        with eng.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT DISTINCT ON (name) name, finished_at, status, started_at "
                    "FROM public.dash_traces "
                    "WHERE kind = 'cron' "
                    "ORDER BY name, COALESCE(finished_at, started_at) DESC"
                )
            ).fetchall()

        crons = []
        for r in rows:
            name, finished_at, status = r[0], r[1], r[2]
            stale = True
            if finished_at is not None:
                # Compare in Python via SQL-derived age would need another query;
                # cheap path: re-check staleness with one bound query below.
                pass
            crons.append(
                {
                    "name": name,
                    "last_run": _iso(finished_at),
                    "status": status,
                    "_finished_raw": finished_at,
                }
            )

        # Determine staleness with a single now()-aware query for accuracy.
        if crons:
            with eng.connect() as conn:
                stale_rows = conn.execute(
                    text(
                        "SELECT DISTINCT ON (name) name, "
                        "(COALESCE(finished_at, started_at) < now() - interval '26 hours') AS stale "
                        "FROM public.dash_traces "
                        "WHERE kind = 'cron' "
                        "ORDER BY name, COALESCE(finished_at, started_at) DESC"
                    )
                ).fetchall()
            stale_map = {sr[0]: bool(sr[1]) for sr in stale_rows}
            for c in crons:
                c["stale"] = stale_map.get(c["name"], True)
                c.pop("_finished_raw", None)

        return {"crons": crons}
    except Exception as e:
        logger.warning("cron-health query failed: %s", e)
        return {"crons": [], "error": str(e)}


@router.get("/traces/agents")
def agent_stats(request: Request, project: str = "", days: int = 7):
    """Per-agent rollup for chat/training kinds.

    Agent is derived from the trace name (split on '.', index 1 when present),
    e.g. "chat.analyst.run_sql" -> "analyst". Aggregated in Python so the agent
    derivation matches `_agent_of` exactly.
    """
    user = _get_user(request)
    _require_super(user)

    days_i = _clamp(days, 7, 1, 365)

    from sqlalchemy import text
    from db import get_sql_engine

    eng = get_sql_engine()
    try:
        params = {"d": days_i}
        where = [
            "kind IN ('chat', 'training')",
            "started_at >= now() - make_interval(days => :d)",
        ]
        if project:
            where.append("project_slug = :proj")
            params["proj"] = project
        where_sql = " AND ".join(where)

        with eng.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT name, status, duration_ms, cost_usd "
                    f"FROM public.dash_traces WHERE {where_sql}"
                ),
                params,
            ).fetchall()

        acc: dict = {}
        for r in rows:
            agent = _agent_of(r[0])
            a = acc.setdefault(
                agent,
                {"agent": agent, "calls": 0, "cost_usd": 0.0, "_ms_sum": 0.0, "_ms_n": 0, "errors": 0},
            )
            a["calls"] += 1
            a["cost_usd"] += float(r[3] or 0)
            if r[2] is not None:
                a["_ms_sum"] += float(r[2])
                a["_ms_n"] += 1
            if r[1] == "error":
                a["errors"] += 1

        agents = []
        for a in acc.values():
            avg_ms = round(a["_ms_sum"] / a["_ms_n"], 2) if a["_ms_n"] else 0.0
            agents.append(
                {
                    "agent": a["agent"],
                    "calls": a["calls"],
                    "cost_usd": round(a["cost_usd"], 6),
                    "avg_ms": avg_ms,
                    "errors": a["errors"],
                }
            )
        agents.sort(key=lambda x: x["calls"], reverse=True)
        return {"agents": agents}
    except Exception as e:
        logger.warning("agent-stats query failed: %s", e)
        return {"agents": [], "error": str(e)}
