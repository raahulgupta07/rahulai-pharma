"""
Database Connectors
===================

Unified connector for PostgreSQL and MySQL.
Allows Dash projects to sync tables from remote databases and run live queries.

Reuses dash_data_sources table (source_type = 'postgresql' | 'mysql').
Credentials stored base64-encoded in config JSONB column.
"""

import base64
import hashlib
import json
import logging
import queue
import re
import threading
import time

import pandas as pd
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool
from starlette.responses import StreamingResponse

from db import db_url
from dash.utils import safe_dumps

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/connectors", tags=["Connectors"])

_engine = create_engine(db_url, poolclass=NullPool)

SUPPORTED_DB_TYPES = {"postgresql", "mysql"}
MAX_ROWS = 10_000
QUERY_TIMEOUT_S = 30


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class TestConnectionRequest(BaseModel):
    host: str
    port: int
    username: str
    password: str
    database: str
    db_type: str  # postgresql | mysql


class ConnectRequest(BaseModel):
    project_slug: str
    host: str
    port: int
    username: str
    password: str
    database: str
    db_type: str
    name: str = ""  # friendly display name
    selected_tables: list[str] = []
    sync_schedule: str = "manual"
    mode: str = "sync"               # sync | live | hybrid
    agent_scope: str = "project"     # project | shared | analyst_only | researcher_only


class SyncRequest(BaseModel):
    source_id: int
    tables: list[str] = []  # empty -> use source's selected_tables
    force: bool = False


class QueryRequest(BaseModel):
    source_id: int
    sql: str


# ---------------------------------------------------------------------------
# Credential encoding helpers
# ---------------------------------------------------------------------------

def _encode(value: str) -> str:
    """Base64-encode a string for storage."""
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def _decode(value: str) -> str:
    """Decode a base64-encoded string."""
    return base64.b64decode(value.encode("ascii")).decode("utf-8")


# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------

def _validate_db_type(db_type: str):
    if db_type not in SUPPORTED_DB_TYPES:
        raise HTTPException(400, f"Unsupported db_type: {db_type}. Use: {', '.join(SUPPORTED_DB_TYPES)}")


def _build_connection_url(config: dict) -> str:
    """Build SQLAlchemy connection URL from config dict (password already decoded)."""
    db_type = config["db_type"]
    user = config["username"]
    password = config["password"]
    host = config["host"]
    port = config["port"]
    database = config["database"]

    if db_type == "postgresql":
        return f"postgresql+psycopg://{user}:{password}@{host}:{port}/{database}"
    elif db_type == "mysql":
        return f"mysql+pymysql://{user}:{password}@{host}:{port}/{database}"
    else:
        raise HTTPException(400, f"Unsupported db_type: {db_type}")


def _get_remote_engine(config: dict):
    """Create a disposable remote engine (NullPool, no caching)."""
    url = _build_connection_url(config)
    connect_args = {"connect_timeout": QUERY_TIMEOUT_S}
    return create_engine(url, poolclass=NullPool, connect_args=connect_args)


def _config_from_source(row) -> dict:
    """Extract and decode config from a dash_data_sources row (config JSONB)."""
    raw = row if isinstance(row, dict) else json.loads(row) if isinstance(row, str) else {}
    if not raw:
        raise HTTPException(400, "Source has no connection config")
    cfg = dict(raw)
    if "password_b64" in cfg:
        cfg["password"] = _decode(cfg["password_b64"])
    return cfg


def _list_tables_sql(db_type: str) -> str:
    if db_type == "postgresql":
        return "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' ORDER BY table_name"
    elif db_type == "mysql":
        return "SHOW TABLES"
    return ""


# ---------------------------------------------------------------------------
# Auth helper (mirrors sharepoint.py)
# ---------------------------------------------------------------------------

def _get_user(request: Request) -> dict:
    user = getattr(getattr(request, "state", None), "user", None)
    if not user:
        raise HTTPException(401, "Not authenticated")
    return user


def _check_editor(user: dict, project_slug: str):
    from app.auth import check_project_permission
    perm = check_project_permission(user, project_slug, required_role="editor")
    if not perm:
        raise HTTPException(403, "Editor access required")
    return perm


def _sanitize_table_name(name: str) -> str:
    """Convert a remote table name to a safe PostgreSQL identifier."""
    name = name.lower()
    name = re.sub(r"[^a-z0-9_]", "_", name)
    name = re.sub(r"_+", "_", name).strip("_")
    if not name or name[0].isdigit():
        name = "t_" + name
    return name[:63]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/test")
def test_connection(req: TestConnectionRequest):
    """Test a database connection. Returns table list on success."""
    _validate_db_type(req.db_type)

    config = {
        "host": req.host, "port": req.port, "username": req.username,
        "password": req.password, "database": req.database, "db_type": req.db_type,
    }

    eng = None
    try:
        eng = _get_remote_engine(config)
        with eng.connect() as conn:
            rows = conn.execute(text(_list_tables_sql(req.db_type))).fetchall()
            tables = [r[0] for r in rows]
        return {"success": True, "tables": tables, "count": len(tables)}
    except Exception as e:
        logger.warning(f"Connection test failed: {e}")
        return {"success": False, "error": str(e)[:300], "tables": []}
    finally:
        if eng:
            eng.dispose()


@router.post("/connect")
def connect_source(req: ConnectRequest, request: Request):
    """Save a database source to dash_data_sources."""
    user = _get_user(request)
    _check_editor(user, req.project_slug)
    _validate_db_type(req.db_type)

    mode_val = (req.mode or "sync").strip().lower()
    if mode_val not in {"sync", "live", "hybrid"}:
        mode_val = "sync"
    scope_val = (req.agent_scope or "project").strip().lower()
    config = {
        "host": req.host, "port": req.port, "username": req.username,
        "password_b64": _encode(req.password), "database": req.database,
        "db_type": req.db_type,
        "selected_tables": list(req.selected_tables or []),
        "sync_schedule": req.sync_schedule or "manual",
        "mode": mode_val,
        "agent_scope": scope_val,
    }

    display_name = req.name or f"{req.db_type}://{req.host}/{req.database}"

    with _engine.connect() as conn:
        row = conn.execute(text(
            "INSERT INTO public.dash_data_sources "
            "(project_slug, user_id, source_type, site_name, config, status) "
            "VALUES (:slug, :uid, :stype, :name, :cfg, 'active') RETURNING id"
        ), {
            "slug": req.project_slug, "uid": user["user_id"],
            "stype": req.db_type, "name": display_name,
            "cfg": json.dumps(config),
        }).fetchone()
        conn.commit()

    return {"status": "connected", "source_id": row[0]}


@router.get("/tables")
def list_tables(request: Request, source_id: int = 0):
    """List tables for a connected remote DB source."""
    user = _get_user(request)

    with _engine.connect() as conn:
        row = conn.execute(text(
            "SELECT config, project_slug FROM public.dash_data_sources "
            "WHERE id = :id AND user_id = :uid AND status = 'active'"
        ), {"id": source_id, "uid": user["user_id"]}).fetchone()

    if not row:
        raise HTTPException(404, "Source not found")

    config = _config_from_source(row[0])
    eng = None
    try:
        eng = _get_remote_engine(config)
        with eng.connect() as conn:
            rows = conn.execute(text(_list_tables_sql(config["db_type"]))).fetchall()
            tables = [r[0] for r in rows]
        return {"tables": tables, "count": len(tables)}
    except Exception as e:
        raise HTTPException(502, f"Failed to list tables: {str(e)[:300]}")
    finally:
        if eng:
            eng.dispose()


@router.get("/schema")
def get_table_schema(request: Request, source_id: int = 0, table: str = ""):
    """Get columns and types for a specific table in the remote DB."""
    user = _get_user(request)
    if not table:
        raise HTTPException(400, "table parameter required")

    with _engine.connect() as conn:
        row = conn.execute(text(
            "SELECT config FROM public.dash_data_sources "
            "WHERE id = :id AND user_id = :uid AND status = 'active'"
        ), {"id": source_id, "uid": user["user_id"]}).fetchone()

    if not row:
        raise HTTPException(404, "Source not found")

    config = _config_from_source(row[0])
    db_type = config["db_type"]

    if db_type == "postgresql":
        sql = (
            "SELECT column_name, data_type, is_nullable "
            "FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = :t ORDER BY ordinal_position"
        )
    elif db_type == "mysql":
        sql = (
            "SELECT column_name, data_type, is_nullable "
            "FROM information_schema.columns "
            "WHERE table_name = :t AND table_schema = DATABASE() ORDER BY ordinal_position"
        )
    else:
        raise HTTPException(400, f"Unsupported db_type: {db_type}")

    eng = None
    try:
        eng = _get_remote_engine(config)
        with eng.connect() as conn:
            rows = conn.execute(text(sql), {"t": table}).fetchall()
        columns = [{"name": r[0], "type": r[1], "nullable": r[2]} for r in rows]
        return {"table": table, "columns": columns, "count": len(columns)}
    except Exception as e:
        raise HTTPException(502, f"Failed to get schema: {str(e)[:300]}")
    finally:
        if eng:
            eng.dispose()


@router.post("/sync")
def sync_tables(req: SyncRequest, request: Request):
    """Sync selected tables from remote DB to project PostgreSQL schema. SSE streaming progress."""
    user = _get_user(request)

    with _engine.connect() as conn:
        row = conn.execute(text(
            "SELECT id, project_slug, config, sync_state "
            "FROM public.dash_data_sources WHERE id = :id AND user_id = :uid AND status = 'active'"
        ), {"id": req.source_id, "uid": user["user_id"]}).fetchone()

    if not row:
        raise HTTPException(404, "Source not found")

    source_id, project_slug, config_raw, sync_state_raw = row
    _check_editor(user, project_slug)

    config = _config_from_source(config_raw)
    sync_state = sync_state_raw if isinstance(sync_state_raw, dict) else json.loads(sync_state_raw) if sync_state_raw else {}
    table_states = sync_state.get("tables", {})

    # Fall back to source's stored selected_tables if request didn't provide any
    if not req.tables:
        req.tables = list(config.get("selected_tables") or [])
        if not req.tables:
            raise HTTPException(400, "No tables to sync. Provide `tables` or set `selected_tables` on the source.")

    accept = request.headers.get("accept", "")
    wants_stream = "text/event-stream" in accept

    progress_q: queue.Queue = queue.Queue()

    def _emit(step: str, detail: str):
        progress_q.put({"step": step, "detail": detail})

    def _sync_worker():
        remote_eng = None
        try:
            remote_eng = _get_remote_engine(config)
            from db.session import create_project_schema, get_project_engine
            schema = create_project_schema(project_slug)
            proj_eng = get_project_engine(project_slug)

            total = len(req.tables)
            _emit("Starting", f"Syncing {total} table(s)")

            for i, tbl_name in enumerate(req.tables):
                safe_name = _sanitize_table_name(tbl_name)
                _emit(f"Reading ({i+1}/{total})", tbl_name)

                try:
                    # Dialect-specific identifier quoting
                    db_type = config.get("db_type", "")
                    if db_type == "mysql":
                        quoted = f"`{tbl_name}`"
                    else:
                        quoted = f'"{tbl_name}"'

                    # Read from remote — limit to MAX_ROWS for safety (dialect-aware)
                    sql_text = text(f"SELECT * FROM {quoted} LIMIT {MAX_ROWS}")
                    with remote_eng.connect() as rconn:
                        if db_type == "postgresql":
                            rconn.execute(text("SET TRANSACTION READ ONLY"))
                        df = pd.read_sql(sql_text, rconn)

                    if df.empty:
                        _emit(f"Skipped ({i+1}/{total})", f"{tbl_name}: empty table")
                        continue

                    # Clean
                    df = _clean_df(df)
                    row_count = len(df)

                    # Change detection: hash of row count + column names + first/last row
                    sig = hashlib.md5(
                        f"{row_count}:{','.join(df.columns)}:{df.iloc[0].to_json() if row_count else ''}".encode()
                    ).hexdigest()

                    prev = table_states.get(safe_name, {})
                    if not req.force and prev.get("hash") == sig and prev.get("rows") == row_count:
                        _emit(f"Unchanged ({i+1}/{total})", f"{tbl_name}: {row_count} rows (no changes)")
                        continue

                    # Write to project schema
                    _emit(f"Writing ({i+1}/{total})", f"{tbl_name} → {schema}.{safe_name} ({row_count} rows)")
                    df.to_sql(safe_name, proj_eng, schema=schema, if_exists="replace", index=False)

                    table_states[safe_name] = {
                        "remote_name": tbl_name, "rows": row_count, "hash": sig,
                        "columns": len(df.columns),
                        "synced_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    }

                    _emit(f"Done ({i+1}/{total})", f"{tbl_name}: {row_count} rows synced")

                except Exception as e:
                    logger.error(f"Sync failed for table {tbl_name}: {e}")
                    _emit(f"Error ({i+1}/{total})", f"{tbl_name}: {str(e)[:150]}")

            # Persist sync state
            new_state = {"tables": table_states, "last_error": ""}
            with _engine.connect() as conn:
                conn.execute(text(
                    "UPDATE public.dash_data_sources SET sync_state = :state, "
                    "last_sync_at = NOW(), updated_at = NOW(), "
                    "config = jsonb_set(COALESCE(config, '{}'), '{selected_tables}', :sel) "
                    "WHERE id = :id"
                ), {
                    "state": json.dumps(new_state), "id": source_id,
                    "sel": json.dumps(req.tables),
                })
                conn.commit()

            synced = sum(1 for t in table_states.values() if t.get("synced_at"))
            _emit("Complete", f"{synced} table(s) synced to schema '{schema}'")

        except Exception as e:
            logger.exception(f"Sync failed for source {source_id}")
            _emit("Error", str(e)[:200])
            try:
                sync_state["last_error"] = str(e)[:500]
                with _engine.connect() as conn:
                    conn.execute(text(
                        "UPDATE public.dash_data_sources SET sync_state = :state, updated_at = NOW() WHERE id = :id"
                    ), {"state": json.dumps(sync_state), "id": source_id})
                    conn.commit()
            except Exception:
                pass
        finally:
            if remote_eng:
                remote_eng.dispose()
            progress_q.put(None)

    thread = threading.Thread(target=_sync_worker, daemon=True)
    thread.start()

    if wants_stream:
        def event_generator():
            while True:
                try:
                    msg = progress_q.get(timeout=300)
                except queue.Empty:
                    yield f"data: {safe_dumps({'step': 'Timeout', 'detail': 'Sync took too long'})}\n\n"
                    break
                if msg is None:
                    break
                yield f"data: {safe_dumps(msg)}\n\n"
            thread.join(timeout=10)

        return StreamingResponse(event_generator(), media_type="text/event-stream")
    else:
        thread.join(timeout=600)
        return {"status": "sync_complete"}


@router.get("/sources")
def list_sources(request: Request, project: str = ""):
    """List all DB connector sources for a project."""
    user = _get_user(request)

    with _engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, source_type, site_name, config, sync_state, last_sync_at, status, created_at "
            "FROM public.dash_data_sources "
            "WHERE project_slug = :slug AND user_id = :uid "
            "AND source_type IN ('postgresql', 'mysql') "
            "AND status != 'deleted' ORDER BY created_at DESC"
        ), {"slug": project, "uid": user["user_id"]}).fetchall()

    sources = []
    for r in rows:
        cfg = r[3] if isinstance(r[3], dict) else json.loads(r[3]) if r[3] else {}
        sync_state = r[4] if isinstance(r[4], dict) else json.loads(r[4]) if r[4] else {}
        table_states = sync_state.get("tables", {})
        sources.append({
            "id": r[0], "db_type": r[1], "source_type": r[1], "name": r[2],
            "host": cfg.get("host", ""), "port": cfg.get("port", ""),
            "database": cfg.get("database", ""),
            "mode": cfg.get("mode", "sync"),
            "agent_scope": cfg.get("agent_scope", "project"),
            "selected_tables": list(cfg.get("selected_tables") or []),
            "synced_tables": list(table_states.keys()),
            "tables_synced": len(table_states),
            "total_rows": sum(t.get("rows", 0) for t in table_states.values()),
            "last_sync_at": str(r[5]) if r[5] else None,
            "status": r[6], "created_at": str(r[7]) if r[7] else None,
            "last_error": sync_state.get("last_error", ""),
            "config": {
                "host": cfg.get("host", ""), "port": cfg.get("port", ""),
                "database": cfg.get("database", ""), "db_type": r[1],
                "mode": cfg.get("mode", "sync"),
                "agent_scope": cfg.get("agent_scope", "project"),
                "selected_tables": list(cfg.get("selected_tables") or []),
            },
        })

    return {"sources": sources}


_VALID_MODES = {"sync", "live", "hybrid"}
_VALID_SCOPES = {"project", "shared", "analyst_only", "researcher_only"}


@router.patch("/sources/{source_id}")
def patch_source(source_id: int, request: Request, body: dict):
    """Update fields on a source: mode (sync/live/hybrid), agent_scope, name."""
    user = _get_user(request)
    patch = {}
    if "mode" in body:
        m = (body.get("mode") or "").strip().lower()
        if m not in _VALID_MODES:
            raise HTTPException(400, f"mode must be one of {sorted(_VALID_MODES)}")
        patch["mode"] = m
    if "agent_scope" in body:
        s = (body.get("agent_scope") or "").strip().lower()
        if s not in _VALID_SCOPES:
            raise HTTPException(400, f"agent_scope must be one of {sorted(_VALID_SCOPES)}")
        patch["agent_scope"] = s
    new_name = (body.get("name") or "").strip() or None

    if not patch and not new_name:
        return {"ok": True, "noop": True}

    with _engine.connect() as conn:
        row = conn.execute(text(
            "SELECT config FROM public.dash_data_sources WHERE id = :id AND user_id = :uid AND status != 'deleted'"
        ), {"id": source_id, "uid": user["user_id"]}).fetchone()
        if not row:
            raise HTTPException(404, "Source not found")
        cfg = row[0] if isinstance(row[0], dict) else (json.loads(row[0]) if row[0] else {})
        cfg.update(patch)
        conn.execute(text(
            "UPDATE public.dash_data_sources SET config = CAST(:cfg AS jsonb)"
            + (", site_name = :name" if new_name else "")
            + ", updated_at = NOW() WHERE id = :id"
        ), {"cfg": json.dumps(cfg), "id": source_id, **({"name": new_name} if new_name else {})})
        conn.commit()
    return {"ok": True, "source_id": source_id, **patch, **({"name": new_name} if new_name else {})}


@router.delete("/sources/{source_id}")
def delete_source(source_id: int, request: Request):
    """Remove a database connector source."""
    user = _get_user(request)

    with _engine.connect() as conn:
        conn.execute(text(
            "UPDATE public.dash_data_sources SET status = 'deleted', updated_at = NOW() "
            "WHERE id = :id AND user_id = :uid AND source_type IN ('postgresql', 'mysql')"
        ), {"id": source_id, "uid": user["user_id"]})
        conn.commit()

    return {"status": "deleted"}


_VALID_SCHEDULES = {"manual", "hourly", "daily", "weekly"}


class ScheduleUpdateRequest(BaseModel):
    sync_schedule: str


@router.post("/sources/{source_id}/schedule")
def update_source_schedule(source_id: int, req: ScheduleUpdateRequest, request: Request):
    """Update the sync_schedule for a database connector source."""
    user = _get_user(request)
    sched = (req.sync_schedule or "").strip().lower()
    if sched not in _VALID_SCHEDULES:
        raise HTTPException(400, f"Invalid sync_schedule (must be one of {sorted(_VALID_SCHEDULES)})")

    with _engine.connect() as conn:
        row = conn.execute(text(
            "SELECT project_slug FROM public.dash_data_sources WHERE id = :id"
        ), {"id": source_id}).fetchone()
        if not row:
            raise HTTPException(404, "Source not found")
        _check_editor(user, row[0])

        conn.execute(text(
            "UPDATE public.dash_data_sources SET sync_schedule = :s, updated_at = NOW() "
            "WHERE id = :id"
        ), {"s": sched, "id": source_id})
        conn.commit()

    return {"ok": True, "source_id": source_id, "sync_schedule": sched}


@router.post("/query")
def run_query(req: QueryRequest, request: Request):
    """Execute a read-only query on a remote DB source. Max 10000 rows, 30s timeout."""
    user = _get_user(request)

    with _engine.connect() as conn:
        row = conn.execute(text(
            "SELECT config, project_slug FROM public.dash_data_sources "
            "WHERE id = :id AND user_id = :uid AND status = 'active' "
            "AND source_type IN ('postgresql', 'mysql')"
        ), {"id": req.source_id, "uid": user["user_id"]}).fetchone()

    if not row:
        raise HTTPException(404, "Source not found")

    _check_editor(user, row[1])
    config = _config_from_source(row[0])

    # Reject write statements
    sql_upper = req.sql.strip().upper()
    if any(sql_upper.startswith(kw) for kw in ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "TRUNCATE")):
        raise HTTPException(400, "Only SELECT queries are allowed")

    eng = None
    try:
        eng = _get_remote_engine(config)
        with eng.connect() as conn:
            if config["db_type"] == "postgresql":
                conn.execute(text("SET TRANSACTION READ ONLY"))
                conn.execute(text(f"SET statement_timeout = '{QUERY_TIMEOUT_S * 1000}'"))
            elif config["db_type"] == "mysql":
                conn.execute(text(f"SET SESSION MAX_EXECUTION_TIME = {QUERY_TIMEOUT_S * 1000}"))

            df = pd.read_sql(text(req.sql), conn)

        # Enforce row limit
        truncated = len(df) > MAX_ROWS
        if truncated:
            df = df.head(MAX_ROWS)

        columns = [{"name": c, "type": str(df[c].dtype)} for c in df.columns]
        records = df.where(df.notna(), None).to_dict(orient="records")

        return {
            "columns": columns, "data": records,
            "row_count": len(records), "truncated": truncated,
        }

    except Exception as e:
        raise HTTPException(400, f"Query failed: {str(e)[:300]}")
    finally:
        if eng:
            eng.dispose()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def sync_worker(source_id: int, tables: list[str] | None = None, force: bool = False) -> dict:
    """Standalone synchronous sync routine for a single source.

    Used by both ad-hoc HTTP triggers and the connector scheduler. Pulls remote
    tables into the project schema, persists ``sync_state`` and ``last_sync_at``.

    Args:
        source_id: dash_data_sources.id
        tables: optional list of table names; falls back to selected_tables
        force: bypass change-detection hash

    Returns:
        dict with ``ok``, ``synced``, ``skipped``, ``errors`` counts and
        ``error`` message on failure.
    """
    remote_eng = None
    try:
        with _engine.connect() as conn:
            row = conn.execute(text(
                "SELECT id, project_slug, config, sync_state "
                "FROM public.dash_data_sources WHERE id = :id AND status = 'active'"
            ), {"id": source_id}).fetchone()
        if not row:
            return {"ok": False, "error": "source not found", "synced": 0, "skipped": 0, "errors": 0}

        sid, project_slug, config_raw, sync_state_raw = row
        config = _config_from_source(config_raw)
        sync_state = sync_state_raw if isinstance(sync_state_raw, dict) else (
            json.loads(sync_state_raw) if sync_state_raw else {}
        )
        table_states = sync_state.get("tables", {})

        target_tables = list(tables or []) or list(config.get("selected_tables") or [])
        if not target_tables:
            return {"ok": False, "error": "no tables to sync", "synced": 0, "skipped": 0, "errors": 0}

        remote_eng = _get_remote_engine(config)
        from db.session import create_project_schema, get_project_engine
        schema = create_project_schema(project_slug)
        proj_eng = get_project_engine(project_slug)

        synced = skipped = errors = 0
        db_type = config.get("db_type", "")

        for tbl_name in target_tables:
            safe_name = _sanitize_table_name(tbl_name)
            try:
                if db_type == "mysql":
                    quoted = f"`{tbl_name}`"
                else:
                    quoted = f'"{tbl_name}"'

                _sql = text(f"SELECT * FROM {quoted} LIMIT {MAX_ROWS}")
                with remote_eng.connect() as rconn:
                    if db_type == "postgresql":
                        rconn.execute(text("SET TRANSACTION READ ONLY"))
                    df = pd.read_sql(_sql, rconn)

                if df.empty:
                    skipped += 1
                    continue

                df = _clean_df(df)
                row_count = len(df)
                sig = hashlib.md5(
                    f"{row_count}:{','.join(df.columns)}:{df.iloc[0].to_json() if row_count else ''}".encode()
                ).hexdigest()

                prev = table_states.get(safe_name, {})
                if not force and prev.get("hash") == sig and prev.get("rows") == row_count:
                    skipped += 1
                    continue

                df.to_sql(safe_name, proj_eng, schema=schema, if_exists="replace", index=False)
                table_states[safe_name] = {
                    "remote_name": tbl_name, "rows": row_count, "hash": sig,
                    "columns": len(df.columns),
                    "synced_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                }
                synced += 1
            except Exception as e:
                logger.warning(f"sync_worker: table {tbl_name} failed: {e}")
                errors += 1

        new_state = {"tables": table_states, "last_error": ""}
        with _engine.connect() as conn:
            conn.execute(text(
                "UPDATE public.dash_data_sources SET sync_state = :state, "
                "last_sync_at = NOW(), updated_at = NOW(), "
                "config = jsonb_set(COALESCE(config, '{}'), '{selected_tables}', :sel) "
                "WHERE id = :id"
            ), {"state": json.dumps(new_state), "id": sid, "sel": json.dumps(target_tables)})
            conn.commit()

        return {"ok": True, "synced": synced, "skipped": skipped, "errors": errors,
                "project_slug": project_slug}
    except Exception as e:
        logger.exception(f"sync_worker failed for source {source_id}")
        try:
            with _engine.connect() as conn:
                conn.execute(text(
                    "UPDATE public.dash_data_sources SET last_error = :err, updated_at = NOW() WHERE id = :id"
                ), {"err": str(e)[:500], "id": source_id})
                conn.commit()
        except Exception:
            pass
        return {"ok": False, "error": str(e)[:300], "synced": 0, "skipped": 0, "errors": 0}
    finally:
        if remote_eng:
            try:
                remote_eng.dispose()
            except Exception:
                pass


def _clean_df(df: pd.DataFrame) -> pd.DataFrame:
    """Lightweight cleanup for synced data."""
    _null_strings = {
        "N/A", "n/a", "#N/A", "NA", "na", "NULL", "null", "None", "none",
        "-", "?", ".", "", "—", "–",
    }
    for col in df.columns:
        if df[col].dtype == object:
            df[col] = df[col].map(lambda x: pd.NA if isinstance(x, str) and x.strip() in _null_strings else x)
    df = df.dropna(how="all")

    # Clean column names
    df.columns = [
        re.sub(r"_+", "_", re.sub(r"[^a-z0-9_]", "_", str(c).lower())).strip("_")[:63]
        for c in df.columns
    ]
    return df


# ---------------------------------------------------------------------------
# Init — called on app startup
# ---------------------------------------------------------------------------

@router.get("/admin/sources")
def admin_list_sources(request: Request):
    """List all DB connector sources across all projects (admin only)."""
    user = _get_user(request)
    from app.auth import SUPER_ADMIN
    if user.get("username") != SUPER_ADMIN:
        raise HTTPException(403, "Admin only")

    try:
        with _engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT id, project_slug, source_type, config, sync_state, last_sync_at, status "
                "FROM public.dash_data_sources "
                "WHERE source_type IN ('postgresql', 'mysql') AND status != 'deleted' "
                "ORDER BY created_at DESC"
            )).fetchall()

        sources = []
        for r in rows:
            cfg = r[3] if isinstance(r[3], dict) else json.loads(r[3]) if r[3] else {}
            sync_state = r[4] if isinstance(r[4], dict) else json.loads(r[4]) if r[4] else {}
            sources.append({
                "id": r[0], "project_slug": r[1], "db_type": r[2],
                "host": cfg.get("host", ""), "database": cfg.get("database", ""),
                "tables": len(cfg.get("selected_tables", [])),
                "last_sync_at": str(r[5]) if r[5] else None, "status": r[6],
            })
        return {"sources": sources}
    except Exception:
        return {"sources": []}


def init_connectors():
    """Ensure dash_data_sources has a config column. No new tables needed.

    The external-DB connector feature is OFF in this single-tenant fork —
    ``public.dash_data_sources`` is never created here (it belongs to the parent
    multi-tenant Dash). Short-circuit when the table is absent so we don't ALTER a
    non-existent table and spam an UndefinedTable WARNING on every boot.
    """
    try:
        with _engine.connect() as conn:
            exists = conn.execute(text(
                "SELECT to_regclass('public.dash_data_sources')"
            )).scalar()
            if not exists:
                logger.debug("Connectors init: dash_data_sources absent (feature off) — skipped")
                return
            # Add config JSONB column if missing (table created by sharepoint.py)
            conn.execute(text("""
                ALTER TABLE public.dash_data_sources
                    ADD COLUMN IF NOT EXISTS config JSONB DEFAULT '{}';
            """))
            conn.commit()
        logger.info("Connectors init OK")
    except Exception as e:
        logger.warning(f"Connectors init skipped: {e}")


# ---------------------------------------------------------------------------
# Per-source training (Phase 4)
# ---------------------------------------------------------------------------

@router.post("/sources/{source_id}/train")
async def train_source(source_id: int, request: Request):
    """Run the per-source ProviderTrainer pipeline. Streams SSE events.

    Loads the source row, ensures the project's providers are registered,
    grabs the matching :class:`BaseProvider`, and wraps
    :meth:`ProviderTrainer.run` in an SSE response. Each step yields a
    ``data: {json}\\n\\n`` line; the terminal ``__result__`` event is
    re-emitted as ``event: done``.
    """
    user = _get_user(request)

    with _engine.connect() as conn:
        row = conn.execute(text(
            "SELECT id, project_slug FROM public.dash_data_sources "
            "WHERE id = :id AND user_id = :uid AND status = 'active' "
            "AND source_type IN ('postgresql', 'mysql')"
        ), {"id": source_id, "uid": user["user_id"]}).fetchone()

    if not row:
        raise HTTPException(404, "Source not found")

    project_slug = row[1]
    _check_editor(user, project_slug)

    # Lazy imports — keep cold-start cheap and avoid pulling the trainer
    # into modules that just want connection-test endpoints.
    from dash.providers.registry import get_registry
    from dash.providers.training_steps_v2 import EnhancedProviderTrainer
    from dash.settings import training_llm_call
    from db.session import get_sql_engine

    registry = get_registry()
    await registry.load_for_project(project_slug)
    provider = None
    for p in registry.list_for_project(project_slug):
        try:
            if int(p.id) == int(source_id):
                provider = p
                break
        except (TypeError, ValueError):
            continue

    if provider is None:
        raise HTTPException(503, f"Provider for source {source_id} not loaded")

    trainer = EnhancedProviderTrainer(
        provider=provider,
        dash_engine=get_sql_engine(),
        deep_model_call=training_llm_call,
        source_id=source_id,
    )

    async def event_generator():
        try:
            async for evt in trainer.run():
                if evt.step == "__result__":
                    yield f"event: done\ndata: {evt.to_json()}\n\n"
                else:
                    yield f"data: {evt.to_json()}\n\n"
        except Exception as exc:  # noqa: BLE001
            logger.exception("train_source streaming failed")
            err = json.dumps({"step": "__error__", "status": "error", "message": str(exc)[:300]})
            yield f"data: {err}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/sources/{source_id}/training-runs")
def list_training_runs(source_id: int, request: Request):
    """Return the last 10 ``dash_source_training_runs`` rows for a source."""
    user = _get_user(request)

    with _engine.connect() as conn:
        owner = conn.execute(text(
            "SELECT project_slug FROM public.dash_data_sources "
            "WHERE id = :id AND user_id = :uid"
        ), {"id": source_id, "uid": user["user_id"]}).fetchone()
        if not owner:
            raise HTTPException(404, "Source not found")

        rows = conn.execute(text(
            "SELECT id, status, current_step, total_steps, cost_usd, "
            "       duration_seconds, error, started_at, completed_at "
            "FROM public.dash_source_training_runs "
            "WHERE source_id = :sid ORDER BY started_at DESC LIMIT 10"
        ), {"sid": source_id}).fetchall()

    return {
        "runs": [
            {
                "id": r[0],
                "status": r[1],
                "current_step": r[2],
                "total_steps": r[3],
                "cost_usd": float(r[4]) if r[4] is not None else 0.0,
                "duration_seconds": r[5],
                "error": r[6],
                "started_at": str(r[7]) if r[7] else None,
                "completed_at": str(r[8]) if r[8] else None,
            }
            for r in rows
        ]
    }
