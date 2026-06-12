"""Admin API for S3 auto-sync sources (Integrations → S3 Sync).

Gated to the 'integration' surface (admin + super only — a plain chat user can't
reach it). Credentials are write-only: never returned in GET responses.
"""
from __future__ import annotations

import logging
import threading

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import create_engine as _create_engine, text
from sqlalchemy.pool import NullPool

from db import db_url

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/s3", tags=["S3 Sync"])
_engine = _create_engine(db_url, poolclass=NullPool)


def _guard(request: Request):
    from app.auth import _require_surface
    return _require_surface(request, "integration")


class FileRule(BaseModel):
    pattern: str
    table: str
    action: str = "replace"


class SourceIn(BaseModel):
    name: str
    bucket: str
    prefix: str = ""
    region: str = "us-east-1"
    endpoint_url: str | None = None
    access_key: str | None = None      # write-only
    secret_key: str | None = None      # write-only
    file_map: list[FileRule] = []
    schedule_seconds: int = 300
    retrain_after: bool = True
    enabled: bool = False


def _mask(row: dict) -> dict:
    row = dict(row)
    row.pop("creds_enc", None)
    row["has_credentials"] = bool(row.pop("_has_creds", False))
    return row


@router.get("/sources")
def list_sources(request: Request):
    _guard(request)
    with _engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, project_slug, name, bucket, prefix, region, endpoint_url, "
            "file_map, schedule_seconds, retrain_after, enabled, last_sync_at, last_status, "
            "(creds_enc IS NOT NULL) AS has_creds "
            "FROM public.dash_s3_sources ORDER BY id"
        )).fetchall()
    out = []
    for r in rows:
        out.append({
            "id": r[0], "project_slug": r[1], "name": r[2], "bucket": r[3], "prefix": r[4],
            "region": r[5], "endpoint_url": r[6], "file_map": r[7] or [],
            "schedule_seconds": r[8], "retrain_after": r[9], "enabled": r[10],
            "last_sync_at": str(r[11]) if r[11] else None, "last_status": r[12],
            "has_credentials": bool(r[13]),
        })
    from dash.connectors import s3_client
    return {"sources": out, "boto3_available": s3_client.boto3_available()}


@router.get("/sources/{source_id}")
def get_source_detail(source_id: int, request: Request):
    _guard(request)
    from app.s3_sync import get_source
    src = get_source(source_id)
    if not src:
        raise HTTPException(404, "source not found")
    src.pop("creds_enc", None)
    with _engine.connect() as conn:
        objs = conn.execute(text(
            "SELECT object_key, etag, table_name, rows_loaded, synced_at "
            "FROM public.dash_s3_object_state WHERE source_id=:i ORDER BY synced_at DESC LIMIT 100"
        ), {"i": source_id}).fetchall()
        log = conn.execute(text("SELECT last_log FROM public.dash_s3_sources WHERE id=:i"),
                           {"i": source_id}).fetchone()
    src["objects"] = [{"key": o[0], "etag": o[1], "table": o[2], "rows": o[3],
                       "synced_at": str(o[4]) if o[4] else None} for o in objs]
    src["last_log"] = log[0] if log else None
    return src


@router.post("/sources")
def create_source(body: SourceIn, request: Request):
    _guard(request)
    import json as _json
    from dash.single_agent import locked_slug
    creds_enc = None
    if body.access_key or body.secret_key:
        from app.s3_sync import encrypt_creds
        creds_enc = encrypt_creds(body.access_key or "", body.secret_key or "")
    fmap = _json.dumps([r.dict() for r in body.file_map])
    with _engine.connect() as conn:
        rid = conn.execute(text(
            "INSERT INTO public.dash_s3_sources "
            "(project_slug, name, bucket, prefix, region, endpoint_url, creds_enc, file_map, "
            " schedule_seconds, retrain_after, enabled) "
            "VALUES (:slug,:name,:bucket,:prefix,:region,:ep,:creds, CAST(:fmap AS jsonb), "
            " :sched,:retrain,:enabled) RETURNING id"
        ), {"slug": locked_slug(), "name": body.name, "bucket": body.bucket, "prefix": body.prefix,
            "region": body.region, "ep": body.endpoint_url, "creds": creds_enc, "fmap": fmap,
            "sched": max(body.schedule_seconds, 60), "retrain": body.retrain_after,
            "enabled": body.enabled}).fetchone()
        conn.commit()
    return {"ok": True, "id": rid[0]}


@router.put("/sources/{source_id}")
def update_source(source_id: int, body: SourceIn, request: Request):
    _guard(request)
    import json as _json
    sets = {
        "name": body.name, "bucket": body.bucket, "prefix": body.prefix, "region": body.region,
        "ep": body.endpoint_url, "fmap": _json.dumps([r.dict() for r in body.file_map]),
        "sched": max(body.schedule_seconds, 60), "retrain": body.retrain_after,
        "enabled": body.enabled, "i": source_id,
    }
    creds_clause = ""
    # Only overwrite creds when new ones are supplied (write-only fields).
    if body.access_key or body.secret_key:
        from app.s3_sync import encrypt_creds
        sets["creds"] = encrypt_creds(body.access_key or "", body.secret_key or "")
        creds_clause = ", creds_enc=:creds"
    with _engine.connect() as conn:
        n = conn.execute(text(
            "UPDATE public.dash_s3_sources SET name=:name, bucket=:bucket, prefix=:prefix, "
            "region=:region, endpoint_url=:ep, file_map=CAST(:fmap AS jsonb), schedule_seconds=:sched, "
            "retrain_after=:retrain, enabled=:enabled, updated_at=now()" + creds_clause +
            " WHERE id=:i"
        ), sets).rowcount
        conn.commit()
    if not n:
        raise HTTPException(404, "source not found")
    return {"ok": True}


@router.delete("/sources/{source_id}")
def delete_source(source_id: int, request: Request):
    _guard(request)
    with _engine.connect() as conn:
        conn.execute(text("DELETE FROM public.dash_s3_sources WHERE id=:i"), {"i": source_id})
        conn.commit()
    return {"ok": True}


@router.post("/sources/{source_id}/test")
def test_source(source_id: int, request: Request):
    _guard(request)
    from app.s3_sync import test_connection
    return test_connection(source_id)


@router.post("/sources/{source_id}/sync")
def sync_source(source_id: int, request: Request, force: bool = False):
    """Kick a manual sync now (runs in a background thread, returns immediately)."""
    _guard(request)
    from app.s3_sync import get_source, run_s3_sync
    if not get_source(source_id):
        raise HTTPException(404, "source not found")
    threading.Thread(target=run_s3_sync, args=(source_id, force, "manual"), daemon=True).start()
    return {"ok": True, "status": "started"}
