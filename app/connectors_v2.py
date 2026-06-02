"""User-facing connector read/query API.

Endpoints per connectors_contract §8 (RBAC via dash.connectors.access.can_user_use).
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import text
from starlette.responses import StreamingResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/connections", tags=["connections"])


def _get_user(request: Request) -> dict:
    user = getattr(getattr(request, "state", None), "user", None)
    if not user:
        raise HTTPException(401, "Not authenticated")
    return user


def _enrich_user(user: dict) -> dict:
    """Augment session user with is_super_admin + aad_groups for access checks."""
    out = dict(user)
    out["is_super_admin"] = bool(user.get("is_super_admin") or user.get("is_super"))
    out["aad_groups"] = list(user.get("aad_groups") or [])
    out["id"] = user.get("id") or user.get("user_id")
    return out


def _row_to_dict(row) -> dict:
    d = dict(row._mapping)
    if "id" in d and d["id"] is not None:
        d["id"] = str(d["id"])
    for k in ("created_at", "updated_at"):
        if k in d and d[k] is not None:
            try:
                d[k] = d[k].isoformat()
            except Exception:
                pass
    cfg = d.get("config")
    if isinstance(cfg, str):
        try:
            d["config"] = json.loads(cfg)
        except Exception:
            pass
    for jk in ("users_allowed", "ldap_groups_allowed"):
        v = d.get(jk)
        if isinstance(v, str):
            try:
                d[jk] = json.loads(v)
            except Exception:
                pass
    return d


def _load_row_full(conn_id: str) -> dict:
    from db.session import get_sql_engine

    eng = get_sql_engine()
    with eng.connect() as c:
        row = c.execute(
            text("SELECT * FROM dash.dash_connections WHERE id = :i"),
            {"i": conn_id},
        ).fetchone()
    if not row:
        raise HTTPException(404, "connection not found")
    return _row_to_dict(row)


def _strip_creds(d: dict) -> dict:
    d.pop("credentials", None)
    return d


def _check_access(user: dict, conn: dict):
    from dash.connectors.access import can_user_use

    if not can_user_use(user, conn):
        raise HTTPException(403, "access denied: connection not granted to your user/group")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@router.get("/available")
def list_available(request: Request):
    user = _enrich_user(_get_user(request))
    from db.session import get_sql_engine

    eng = get_sql_engine()
    with eng.connect() as c:
        rows = c.execute(
            text("SELECT * FROM dash.dash_connections WHERE enabled = true ORDER BY name")
        ).fetchall()

    out = []
    from dash.connectors.access import can_user_use

    for r in rows:
        d = _row_to_dict(r)
        if can_user_use(user, d):
            out.append(_strip_creds(d))
    return {"connections": out}


@router.get("/{conn_id}/schema")
def get_schema(conn_id: str, request: Request):
    user = _enrich_user(_get_user(request))
    row = _load_row_full(conn_id)
    _check_access(user, row)
    from dash.connectors import audit, instantiate_client

    t0 = time.time()
    try:
        client = instantiate_client(row)
        schemas = client.get_schemas()
        audit(
            conn_id,
            user.get("id"),
            "schema",
            duration_ms=int((time.time() - t0) * 1000),
        )
        return {"schemas": schemas}
    except Exception as e:
        audit(
            conn_id,
            user.get("id"),
            "schema",
            duration_ms=int((time.time() - t0) * 1000),
            error=str(e),
        )
        raise HTTPException(500, f"schema fetch failed: {e}")


@router.post("/{conn_id}/query")
async def run_query(conn_id: str, request: Request):
    user = _enrich_user(_get_user(request))
    row = _load_row_full(conn_id)
    _check_access(user, row)

    body = await request.json()
    sql = (body.get("sql") or "").strip()
    if not sql:
        raise HTTPException(400, "sql required")
    timeout_s = int(body.get("timeout_s") or 60)
    max_rows = int(body.get("max_rows") or 5000)

    # Read-only SQL gate — reject INSERT/UPDATE/DELETE/DROP/etc BEFORE execute.
    from dash.connectors.safety import is_read_only_sql, safe_error_message

    ok, reason = is_read_only_sql(sql, dialect=row.get("connector_type", ""))
    if not ok:
        raise HTTPException(400, f"read-only gate rejected sql: {reason}")

    # Per-day quota (super-admin bypasses).
    quota = int(row.get("query_limit_per_day") or 1000)
    if not user.get("is_super_admin") and quota > 0:
        from db.session import get_sql_engine as _qse

        _eng = _qse()
        with _eng.connect() as _c:
            used = _c.execute(
                text(
                    "SELECT COUNT(*) FROM dash.dash_connection_audit "
                    "WHERE connection_id = :i AND user_id = :u "
                    "AND action = 'query' AND created_at >= current_date"
                ),
                {"i": conn_id, "u": user.get("id")},
            ).scalar() or 0
        if int(used) >= quota:
            raise HTTPException(
                status_code=429,
                detail={
                    "error": "daily quota exceeded",
                    "limit": quota,
                    "used": int(used),
                    "reset_at": "midnight UTC",
                },
            )

    from dash.connectors import audit, instantiate_client

    # Decrypted creds — used only for scrubbing error messages, never returned.
    creds_for_scrub: dict | None = None
    try:
        from dash.connectors.crypto import decrypt_credentials

        creds_for_scrub = decrypt_credentials(row.get("credentials") or "") or {}
    except Exception:
        creds_for_scrub = None

    t0 = time.time()
    cfg = row.get("config") or {}
    connector_type = row.get("connector_type")
    auth_mode = cfg.get("auth_mode", "service_principal")
    is_powerbi_obo = (connector_type == "powerbi" and auth_mode == "obo")

    try:
        client = instantiate_client(row)
        if is_powerbi_obo:
            from dash.connectors.clients.powerbi_client import set_obo_user
            from dash.connectors.oauth_obo import OBOTokenMissingError

            try:
                with set_obo_user(int(user.get("id"))):
                    df = client.execute_query(sql, timeout_s=timeout_s, max_rows=max_rows)
            except OBOTokenMissingError as miss:
                audit(
                    conn_id,
                    user.get("id"),
                    "query",
                    sql=sql,
                    duration_ms=int((time.time() - t0) * 1000),
                    error=f"obo_missing: {miss}",
                )
                # 428 Precondition Required — UI should redirect to consent URL
                raise HTTPException(
                    status_code=428,
                    detail={
                        "error": "obo_consent_required",
                        "message": str(miss),
                        "consent_url": f"/api/connections/{conn_id}/obo/consent-url",
                    },
                )
        else:
            df = client.execute_query(sql, timeout_s=timeout_s, max_rows=max_rows)
        rows_out = df.to_dict("records")
        cols = list(df.columns)
        audit(
            conn_id,
            user.get("id"),
            "query",
            sql=sql,
            row_count=len(rows_out),
            duration_ms=int((time.time() - t0) * 1000),
        )
        # BigQuery surfaces cost meta from _last_cost_info if dry-run / job billing recorded.
        meta: dict = {}
        if connector_type == "bigquery":
            cost = getattr(client, "_last_cost_info", None)
            if isinstance(cost, dict):
                meta["bytes_processed"] = cost.get("total_bytes_processed")
                meta["bytes_billed"] = cost.get("total_bytes_billed")
                meta["estimated_cost_usd"] = cost.get("estimated_cost_usd")
        return {"rows": rows_out, "columns": cols, "row_count": len(rows_out), "meta": meta}
    except HTTPException:
        raise
    except Exception as e:
        raw_err = str(e)
        scrubbed = safe_error_message(e, creds_for_scrub)
        audit(
            conn_id,
            user.get("id"),
            "query",
            sql=sql,
            duration_ms=int((time.time() - t0) * 1000),
            error=raw_err,  # raw goes to audit log (server-side, trusted)
        )
        raise HTTPException(500, f"query failed: {scrubbed}")


@router.post("/{conn_id}/query/stream")
async def run_query_stream(conn_id: str, request: Request):
    """Stream query results as Server-Sent Events.

    Events: meta → chunk (repeated) → done | error.
    Falls back to execute_query + manual chunking if client raises NotImplementedError
    on execute_query_stream (e.g. PowerBI).
    """
    user = _enrich_user(_get_user(request))
    row = _load_row_full(conn_id)
    _check_access(user, row)

    body = await request.json()
    sql = (body.get("sql") or "").strip()
    if not sql:
        raise HTTPException(400, "sql required")
    chunk_size = int(body.get("chunk_size") or 1000)
    timeout_s = int(body.get("timeout_s") or 60)
    max_rows = int(body.get("max_rows") or 100000)

    from dash.connectors import audit, instantiate_client

    def _sse(event: str, payload: dict) -> str:
        return f"event: {event}\ndata: {json.dumps(payload, default=str)}\n\n"

    async def event_stream():
        t0 = time.time()
        total_rows = 0
        try:
            client = instantiate_client(row)
            columns: list[str] = []
            try:
                # Native streaming path
                iterator = client.execute_query_stream(
                    sql,
                    chunk_size=chunk_size,
                    timeout_s=timeout_s,
                    max_rows=max_rows,
                )
                first = True
                for batch in iterator:
                    if not batch:
                        continue
                    if first:
                        columns = list(batch[0].keys())
                        yield _sse("meta", {"columns": columns, "estimated_rows": None})
                        first = False
                    total_rows += len(batch)
                    yield _sse("chunk", {"rows": batch, "count": len(batch)})
                if first:
                    # Empty result — still emit meta
                    yield _sse("meta", {"columns": [], "estimated_rows": 0})
            except NotImplementedError:
                # Fallback: single execute_query + split into chunks (e.g. PowerBI)
                df = client.execute_query(sql, timeout_s=timeout_s, max_rows=max_rows)
                columns = list(df.columns)
                all_rows = df.to_dict("records")
                yield _sse(
                    "meta",
                    {"columns": columns, "estimated_rows": len(all_rows)},
                )
                for i in range(0, len(all_rows), chunk_size):
                    batch = all_rows[i : i + chunk_size]
                    total_rows += len(batch)
                    yield _sse("chunk", {"rows": batch, "count": len(batch)})

            duration_ms = int((time.time() - t0) * 1000)
            audit(
                conn_id,
                user.get("id"),
                "query_stream",
                sql=sql,
                row_count=total_rows,
                duration_ms=duration_ms,
            )
            yield _sse("done", {"total_rows": total_rows, "duration_ms": duration_ms})
        except Exception as e:
            duration_ms = int((time.time() - t0) * 1000)
            audit(
                conn_id,
                user.get("id"),
                "query_stream",
                sql=sql,
                row_count=total_rows,
                duration_ms=duration_ms,
                error=str(e),
            )
            yield _sse("error", {"message": str(e)})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/{conn_id}/query/estimate")
async def estimate_query_cost(conn_id: str, request: Request):
    """Dry-run cost estimate (BigQuery only). Returns bytes_processed + estimated_cost_usd."""
    user = _enrich_user(_get_user(request))
    row = _load_row_full(conn_id)
    _check_access(user, row)

    if row.get("connector_type") != "bigquery":
        return {"supported": False, "reason": f"Cost estimation only supported for bigquery, got {row.get('connector_type')}"}

    body = await request.json()
    sql = (body.get("sql") or "").strip()
    if not sql:
        raise HTTPException(400, "sql required")

    from dash.connectors import instantiate_client

    try:
        client = instantiate_client(row)
        cost = client.estimate_cost(sql)
        cap = row.get("max_bytes_per_query")
        return {
            "supported": True,
            "bytes_processed": cost["total_bytes_processed"],
            "bytes_billed": cost["total_bytes_billed"],
            "estimated_cost_usd": cost["estimated_cost_usd"],
            "cap_bytes": cap,
            "would_run": (cap is None or cost["total_bytes_processed"] <= cap),
        }
    except Exception as e:
        raise HTTPException(500, f"estimate failed: {e}")
