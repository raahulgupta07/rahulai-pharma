"""Super-admin CRUD for the connector subsystem.

Endpoints per connectors_contract §8 (super-admin only).
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import text

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin-connectors"])


# ---------------------------------------------------------------------------
# Auth helpers (mirror app/admin_api.py)
# ---------------------------------------------------------------------------
def _get_user(request: Request) -> dict:
    user = getattr(getattr(request, "state", None), "user", None)
    if not user:
        raise HTTPException(401, "Not authenticated")
    return user


def _require_super(user: dict):
    if not user.get("is_super_admin") and not user.get("is_super"):
        raise HTTPException(403, "super-admin only")


# ---------------------------------------------------------------------------
# Local helpers
# ---------------------------------------------------------------------------
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


def _strip_creds(d: dict) -> dict:
    d.pop("credentials", None)
    return d


def _load_row(conn_id: str) -> dict:
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


def _validate_payload(connector_type: str, config: dict, credentials: dict):
    from dash.connectors.registry import REGISTRY

    if connector_type not in REGISTRY:
        raise HTTPException(400, f"unknown connector_type: {connector_type}")
    entry = REGISTRY[connector_type]
    try:
        entry.config_schema(**(config or {}))
    except Exception as e:
        raise HTTPException(400, f"invalid config: {e}")
    try:
        entry.credentials_schema(**(credentials or {}))
    except Exception as e:
        raise HTTPException(400, f"invalid credentials: {e}")


# ---------------------------------------------------------------------------
# Registry endpoints
# ---------------------------------------------------------------------------
@router.get("/connectors")
def list_connector_types(request: Request):
    _require_super(_get_user(request))
    from dash.connectors.registry import list_connectors

    return {"connectors": list_connectors()}


@router.get("/connectors/{ctype}/fields")
def get_connector_fields(ctype: str, request: Request):
    _require_super(_get_user(request))
    from dash.connectors.registry import REGISTRY

    if ctype not in REGISTRY:
        raise HTTPException(404, f"unknown connector_type: {ctype}")
    entry = REGISTRY[ctype]
    return {
        "type": entry.type,
        "title": entry.title,
        "kind": entry.kind,
        "description": entry.description,
        "config": entry.config_schema.model_json_schema(),
        "credentials": entry.credentials_schema.model_json_schema(),
    }


# ---------------------------------------------------------------------------
# Connections CRUD
# ---------------------------------------------------------------------------
@router.get("/connections")
def list_connections(request: Request):
    _require_super(_get_user(request))
    from db.session import get_sql_engine

    eng = get_sql_engine()
    with eng.connect() as c:
        rows = c.execute(
            text(
                "SELECT id, name, connector_type, config, owner_user_id, enabled, "
                "allow_all_users, users_allowed, ldap_groups_allowed, created_at, updated_at "
                "FROM dash.dash_connections ORDER BY created_at DESC"
            )
        ).fetchall()
    return {"connections": [_row_to_dict(r) for r in rows]}


@router.post("/connections")
async def create_connection(request: Request):
    user = _get_user(request)
    _require_super(user)
    body = await request.json()
    name = (body.get("name") or "").strip()
    ctype = (body.get("connector_type") or "").strip()
    config = body.get("config") or {}
    credentials = body.get("credentials") or {}
    if not name or not ctype:
        raise HTTPException(400, "name + connector_type required")
    _validate_payload(ctype, config, credentials)

    from dash.connectors.crypto import encrypt_credentials
    from db.session import get_write_engine

    enc = encrypt_credentials(credentials)
    quota = int(body.get("query_limit_per_day") or 1000)
    mbq = body.get("max_bytes_per_query")
    mbq = None if mbq is None else int(mbq)
    eng = get_write_engine()
    with eng.begin() as c:
        row = c.execute(
            text(
                """
                INSERT INTO dash.dash_connections
                (name, connector_type, config, credentials, owner_user_id, enabled,
                 allow_all_users, users_allowed, ldap_groups_allowed,
                 query_limit_per_day, max_bytes_per_query)
                VALUES (:n, :t, CAST(:c AS jsonb), :cr, :o, true, false,
                        CAST(:ua AS jsonb), CAST(:lg AS jsonb),
                        :qlim, :mbq)
                RETURNING id, name, connector_type, config, owner_user_id, enabled,
                          allow_all_users, users_allowed, ldap_groups_allowed,
                          query_limit_per_day, max_bytes_per_query,
                          created_at, updated_at
                """
            ),
            {
                "n": name,
                "t": ctype,
                "c": json.dumps(config),
                "cr": enc,
                "o": user.get("user_id"),
                "ua": json.dumps([]),
                "lg": json.dumps([]),
                "qlim": quota,
                "mbq": mbq,
            },
        ).fetchone()
    return {"connection": _row_to_dict(row)}


@router.patch("/connections/{conn_id}")
async def patch_connection(conn_id: str, request: Request):
    _require_super(_get_user(request))
    body = await request.json()
    existing = _load_row(conn_id)

    sets: list[str] = []
    params: dict[str, Any] = {"i": conn_id}

    if "name" in body and body["name"]:
        sets.append("name = :n")
        params["n"] = body["name"].strip()
    if "enabled" in body:
        sets.append("enabled = :e")
        params["e"] = bool(body["enabled"])
    if "config" in body and isinstance(body["config"], dict):
        # Validate combined config against existing connector_type schema
        from dash.connectors.registry import REGISTRY

        entry = REGISTRY.get(existing["connector_type"])
        if entry:
            try:
                entry.config_schema(**body["config"])
            except Exception as e:
                raise HTTPException(400, f"invalid config: {e}")
        sets.append("config = CAST(:c AS jsonb)")
        params["c"] = json.dumps(body["config"])
    if "credentials" in body and isinstance(body["credentials"], dict):
        from dash.connectors.crypto import encrypt_credentials
        from dash.connectors.registry import REGISTRY

        entry = REGISTRY.get(existing["connector_type"])
        if entry:
            try:
                entry.credentials_schema(**body["credentials"])
            except Exception as e:
                raise HTTPException(400, f"invalid credentials: {e}")
        sets.append("credentials = :cr")
        params["cr"] = encrypt_credentials(body["credentials"])
        # Reset rotation marker on cred change
        sets.append("secret_rotated_at = now()")
        sets.append("last_rotation_warning_at = NULL")
    if "query_limit_per_day" in body:
        sets.append("query_limit_per_day = :qlim")
        params["qlim"] = int(body["query_limit_per_day"])
    if "max_bytes_per_query" in body:
        sets.append("max_bytes_per_query = :mbq")
        params["mbq"] = (
            None if body["max_bytes_per_query"] is None else int(body["max_bytes_per_query"])
        )

    if not sets:
        return {"connection": _strip_creds(existing)}

    sets.append("updated_at = now()")
    from db.session import get_write_engine

    eng = get_write_engine()
    with eng.begin() as c:
        c.execute(
            text(f"UPDATE dash.dash_connections SET {', '.join(sets)} WHERE id = :i"),
            params,
        )
    return {"connection": _strip_creds(_load_row(conn_id))}


@router.delete("/connections/{conn_id}")
def delete_connection(conn_id: str, request: Request):
    _require_super(_get_user(request))
    from db.session import get_write_engine

    eng = get_write_engine()
    with eng.begin() as c:
        res = c.execute(
            text("DELETE FROM dash.dash_connections WHERE id = :i"),
            {"i": conn_id},
        )
    return {"ok": True, "deleted": res.rowcount}


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------
@router.post("/connections/test")
async def test_transient(request: Request):
    _require_super(_get_user(request))
    body = await request.json()
    ctype = body.get("connector_type")
    config = body.get("config") or {}
    credentials = body.get("credentials") or {}
    _validate_payload(ctype, config, credentials)
    from dash.connectors.registry import resolve_client_class

    from dash.connectors.safety import safe_error_message

    try:
        cls = resolve_client_class(ctype)
        client = cls(**config, **credentials)
        return {"ok": True, "result": client.test_connection()}
    except Exception as e:
        return {"ok": False, "error": safe_error_message(e, credentials or {})}


@router.post("/connections/{conn_id}/test")
def test_saved(conn_id: str, request: Request):
    _require_super(_get_user(request))
    row = _load_row(conn_id)
    from dash.connectors import audit, instantiate_client
    from dash.connectors.crypto import decrypt_credentials
    from dash.connectors.safety import safe_error_message

    creds: dict | None = None
    try:
        creds = decrypt_credentials(row.get("credentials") or "") or {}
    except Exception:
        creds = None

    t0 = time.time()
    try:
        client = instantiate_client(row)
        result = client.test_connection()
        audit(conn_id, _get_user(request).get("user_id"), "test",
              duration_ms=int((time.time() - t0) * 1000))
        return {"ok": True, "result": result}
    except Exception as e:
        audit(conn_id, _get_user(request).get("user_id"), "test",
              duration_ms=int((time.time() - t0) * 1000), error=str(e))
        return {"ok": False, "error": safe_error_message(e, creds)}


# ---------------------------------------------------------------------------
# Grant
# ---------------------------------------------------------------------------
@router.post("/connections/{conn_id}/grant")
async def grant_connection(conn_id: str, request: Request):
    _require_super(_get_user(request))
    body = await request.json()
    _load_row(conn_id)  # 404 if missing

    sets: list[str] = []
    params: dict[str, Any] = {"i": conn_id}
    if "allow_all_users" in body:
        sets.append("allow_all_users = :a")
        params["a"] = bool(body["allow_all_users"])
    if "users_allowed" in body and isinstance(body["users_allowed"], list):
        sets.append("users_allowed = CAST(:ua AS jsonb)")
        params["ua"] = json.dumps([int(x) for x in body["users_allowed"]])
    if "ldap_groups_allowed" in body and isinstance(body["ldap_groups_allowed"], list):
        sets.append("ldap_groups_allowed = CAST(:lg AS jsonb)")
        params["lg"] = json.dumps([str(x) for x in body["ldap_groups_allowed"]])
    if not sets:
        raise HTTPException(400, "no grant fields provided")
    sets.append("updated_at = now()")

    from db.session import get_write_engine

    eng = get_write_engine()
    with eng.begin() as c:
        c.execute(
            text(f"UPDATE dash.dash_connections SET {', '.join(sets)} WHERE id = :i"),
            params,
        )
    return {"connection": _strip_creds(_load_row(conn_id))}


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------
@router.get("/connections/{conn_id}/audit")
def get_audit(conn_id: str, request: Request, limit: int = 100):
    _require_super(_get_user(request))
    from db.session import get_sql_engine

    limit = max(1, min(int(limit or 100), 1000))
    eng = get_sql_engine()
    with eng.connect() as c:
        rows = c.execute(
            text(
                "SELECT id, connection_id, user_id, action, sql_text, row_count, "
                "duration_ms, error, created_at "
                "FROM dash.dash_connection_audit "
                "WHERE connection_id = :i ORDER BY created_at DESC LIMIT :l"
            ),
            {"i": conn_id, "l": limit},
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r._mapping)
        if d.get("created_at") is not None:
            try:
                d["created_at"] = d["created_at"].isoformat()
            except Exception:
                pass
        if d.get("connection_id") is not None:
            d["connection_id"] = str(d["connection_id"])
        out.append(d)
    return {"audit": out}


# ---------------------------------------------------------------------------
# Phase 2: secret rotation
# ---------------------------------------------------------------------------
@router.post("/connections/{conn_id}/rotate-secret")
async def rotate_secret(conn_id: str, request: Request):
    """Rotate credentials for a connection. Body: {credentials: dict}. Super-admin only."""
    _require_super(_get_user(request))
    body = await request.json()
    creds_dict = body.get("credentials")
    if not isinstance(creds_dict, dict) or not creds_dict:
        raise HTTPException(400, "credentials dict required")

    from dash.connectors.crypto import encrypt_credentials
    from dash.connectors.registry import REGISTRY
    from db.session import get_sql_engine, get_write_engine

    eng = get_sql_engine()
    with eng.connect() as c:
        row = c.execute(
            text("SELECT connector_type FROM dash.dash_connections WHERE id = :i"),
            {"i": conn_id},
        ).fetchone()
    if not row:
        raise HTTPException(404, "connection not found")

    # Validate against schema
    entry = REGISTRY.get(row[0])
    if entry:
        try:
            entry.credentials_schema(**creds_dict)
        except Exception as e:
            raise HTTPException(400, f"credentials validation failed: {e}")

    enc = encrypt_credentials(creds_dict)
    weng = get_write_engine()
    with weng.begin() as c:
        c.execute(
            text(
                "UPDATE dash.dash_connections "
                "SET credentials = :c, secret_rotated_at = now(), "
                "last_rotation_warning_at = NULL, updated_at = now() "
                "WHERE id = :i"
            ),
            {"c": enc, "i": conn_id},
        )
    return {"ok": True, "rotated_at": time.time()}


@router.get("/connections/rotation-status")
def rotation_status(request: Request):
    """Per-connection secret age + days-until-warning + severity. Super-admin only."""
    _require_super(_get_user(request))
    from db.session import get_sql_engine

    eng = get_sql_engine()
    with eng.connect() as c:
        rows = c.execute(
            text(
                "SELECT id, name, connector_type, secret_rotated_at, "
                "secret_rotation_alert_days, last_rotation_warning_at, "
                "EXTRACT(EPOCH FROM (now() - secret_rotated_at)) / 86400 AS days_since "
                "FROM dash.dash_connections "
                "ORDER BY secret_rotated_at ASC NULLS FIRST"
            )
        ).fetchall()

    out = []
    for r in rows:
        d = dict(r._mapping)
        d["id"] = str(d["id"])
        days_since = float(d.get("days_since") or 0)
        alert_days = int(d.get("secret_rotation_alert_days") or 90)
        d["days_since"] = round(days_since, 1)
        d["next_warning_in_days"] = max(0, round(alert_days - days_since, 1))
        if days_since >= alert_days + 30:
            d["severity"] = "critical"
        elif days_since >= alert_days:
            d["severity"] = "warn"
        else:
            d["severity"] = "ok"
        for k in ("secret_rotated_at", "last_rotation_warning_at"):
            v = d.get(k)
            if v is not None:
                try:
                    d[k] = v.isoformat()
                except Exception:
                    pass
        out.append(d)
    return {"connections": out}
