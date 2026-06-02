"""Federation health endpoints — analytics over dash_audit_log."""
import logging
from fastapi import APIRouter, HTTPException, Request

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/federation", tags=["federation_health"])


def _get_user(request: Request) -> dict:
    user = getattr(getattr(request, "state", None), "user", None)
    if not user:
        raise HTTPException(401)
    return user


@router.get("/health/{project_slug}")
def project_health(project_slug: str, request: Request, days: int = 7):
    """Per-project federation health."""
    user = _get_user(request)
    try:
        from app.auth import check_project_permission
        if not check_project_permission(user, project_slug, required_role="viewer"):
            raise HTTPException(403)
    except ImportError:
        pass

    try:
        from sqlalchemy import text
        from db.session import get_sql_engine
        with get_sql_engine().connect() as conn:
            # Total federated queries in window
            r = conn.execute(text(
                "SELECT COUNT(*), AVG(latency_ms), AVG(row_count), SUM(cost_usd) "
                "FROM public.dash_audit_log "
                "WHERE project_slug = :s AND action = 'federated_query' "
                "  AND created_at > NOW() - (:d * INTERVAL '1 day')"
            ), {"s": project_slug, "d": days}).fetchone()
            total, avg_latency, avg_rows, total_cost = r

            # Per-day buckets
            buckets = conn.execute(text(
                "SELECT DATE(created_at), COUNT(*), AVG(latency_ms) "
                "FROM public.dash_audit_log "
                "WHERE project_slug = :s AND action = 'federated_query' "
                "  AND created_at > NOW() - (:d * INTERVAL '1 day') "
                "GROUP BY DATE(created_at) ORDER BY 1 DESC LIMIT 30"
            ), {"s": project_slug, "d": days}).fetchall()

            # Recent failures (look for ERROR in details)
            failures = conn.execute(text(
                "SELECT created_at, sources_used, details "
                "FROM public.dash_audit_log "
                "WHERE project_slug = :s AND action = 'federated_query' "
                "  AND (details::text LIKE '%error%' OR details::text LIKE '%fail%') "
                "ORDER BY created_at DESC LIMIT 10"
            ), {"s": project_slug}).fetchall()

        # Circuit breaker state
        cb_state = None
        try:
            from dash.providers.federation.circuit_breaker import check
            state = check(project_slug)
            cb_state = {
                "is_open": state.is_open,
                "open_until": state.open_until.isoformat() if state.open_until else None,
                "consecutive_failures": state.consecutive_failures,
                "last_error": state.last_error,
            }
        except Exception:
            pass

        return {
            "project_slug": project_slug,
            "window_days": days,
            "total_queries": int(total or 0),
            "avg_latency_ms": float(avg_latency or 0),
            "avg_rows": float(avg_rows or 0),
            "total_cost_usd": float(total_cost or 0),
            "daily_buckets": [
                {"day": str(b[0]), "count": int(b[1]), "avg_latency_ms": float(b[2] or 0)}
                for b in buckets
            ],
            "recent_failures": [
                {
                    "ts": str(f[0]),
                    "sources": f[1] if f[1] else [],
                    "details": str(f[2])[:500],
                }
                for f in failures
            ],
            "circuit_breaker": cb_state,
        }
    except Exception as e:
        return {"error": str(e)[:200]}


@router.post("/circuit/{project_slug}/reset")
async def reset_circuit_breaker(project_slug: str, request: Request):
    """Manually reset federation circuit breaker (admin only)."""
    user = _get_user(request)
    if not user.get("is_super_admin"):
        try:
            from app.auth import check_project_permission
            if not check_project_permission(user, project_slug, required_role="admin"):
                raise HTTPException(403)
        except ImportError:
            raise HTTPException(403)

    try:
        from dash.providers.federation.circuit_breaker import reset
        ok = reset(project_slug)
        return {"reset": ok}
    except Exception as e:
        return {"reset": False, "error": str(e)[:200]}


@router.get("/admin/all-projects")
def admin_all_projects(request: Request, days: int = 7):
    """Cross-project federation health (super-admin only)."""
    user = _get_user(request)
    if not user.get("is_super_admin"):
        raise HTTPException(403)

    try:
        from sqlalchemy import text
        from db.session import get_sql_engine
        with get_sql_engine().connect() as conn:
            rows = conn.execute(text(
                "SELECT project_slug, COUNT(*), AVG(latency_ms), AVG(row_count), "
                "       SUM(cost_usd) "
                "FROM public.dash_audit_log "
                "WHERE action = 'federated_query' "
                "  AND created_at > NOW() - (:d * INTERVAL '1 day') "
                "GROUP BY project_slug ORDER BY 2 DESC"
            ), {"d": days}).fetchall()

            # Open circuits
            circuits = conn.execute(text(
                "SELECT project_slug, consecutive_failures, open_until, last_error "
                "FROM public.dash_federation_circuit "
                "WHERE open_until > NOW()"
            )).fetchall()

        return {
            "window_days": days,
            "by_project": [
                {
                    "slug": r[0],
                    "count": int(r[1]),
                    "avg_latency_ms": float(r[2] or 0),
                    "avg_rows": float(r[3] or 0),
                    "total_cost_usd": float(r[4] or 0),
                }
                for r in rows
            ],
            "open_circuits": [
                {
                    "slug": c[0],
                    "consecutive_failures": int(c[1] or 0),
                    "open_until": str(c[2]) if c[2] else None,
                    "last_error": c[3],
                }
                for c in circuits
            ],
        }
    except Exception as e:
        return {"error": str(e)[:200]}
