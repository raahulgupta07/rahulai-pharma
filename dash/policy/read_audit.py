"""Phase 7A — cross-store visibility read audit log.

Non-blocking queue + daemon flusher pattern (see dash/rls/audit.py).
Logs only cross-scope reads (intent != 'private').
"""
from __future__ import annotations

import csv
import io
import logging
import queue
import threading
import time
from datetime import datetime
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from db import db_url

_log = logging.getLogger(__name__)
_QUEUE: queue.Queue = queue.Queue(maxsize=1000)
_FLUSH_DAEMON_STARTED = False
_LOCK = threading.Lock()


def _ensure_table_once():
    try:
        from .loader import _ensure_visibility_policy_table
        _ensure_visibility_policy_table()
    except Exception as e:
        _log.warning(f"read_audit: ensure_table failed: {e}")


def log_read(project_slug: str,
             viewer_user_id: int | None,
             viewer_scope_id: str | None,
             target_scope_id: str | None,
             intent: str,
             policy_version: int | None,
             sql: str | None,
             fields_downgraded: list[str] | None,
             row_count: int | None = None) -> None:
    """Enqueue a cross-store read event. Non-blocking; drops oldest on overflow."""
    if (intent or "").lower() == "private":
        return
    payload = {
        "project_slug": project_slug,
        "viewer_user_id": viewer_user_id,
        "viewer_scope_id": viewer_scope_id,
        "target_scope_id": target_scope_id,
        "intent": intent,
        "policy_version": policy_version,
        "sql_excerpt": (sql or "")[:2000] if sql else None,
        "fields_downgraded": list(fields_downgraded) if fields_downgraded else None,
        "row_count": row_count,
    }
    try:
        _QUEUE.put_nowait(payload)
    except queue.Full:
        try:
            _QUEUE.get_nowait()  # drop oldest
            _QUEUE.put_nowait(payload)
        except Exception:
            pass
    _start_flusher()


def _start_flusher():
    global _FLUSH_DAEMON_STARTED
    if _FLUSH_DAEMON_STARTED:
        return
    with _LOCK:
        if _FLUSH_DAEMON_STARTED:
            return
        _FLUSH_DAEMON_STARTED = True
        t = threading.Thread(target=_flusher_loop, daemon=True, name="visibility-read-audit")
        t.start()


def _flusher_loop():
    _ensure_table_once()
    eng = create_engine(db_url, poolclass=NullPool)
    while True:
        try:
            batch: list[dict] = []
            try:
                batch.append(_QUEUE.get(timeout=30))
            except queue.Empty:
                continue
            while len(batch) < 200:
                try:
                    batch.append(_QUEUE.get_nowait())
                except queue.Empty:
                    break
            with eng.begin() as conn:
                conn.execute(text("""
                    INSERT INTO public.dash_visibility_read_log
                      (project_slug, viewer_user_id, viewer_scope_id, target_scope_id,
                       intent, policy_version, sql_excerpt, fields_downgraded, row_count)
                    VALUES
                      (:project_slug, :viewer_user_id, :viewer_scope_id, :target_scope_id,
                       :intent, :policy_version, :sql_excerpt, :fields_downgraded, :row_count)
                """), batch)
        except Exception as e:
            _log.warning(f"read_audit flush failed: {e}")
            time.sleep(5)


def _build_filters(project_slug, target_scope=None, viewer_user_id=None,
                   from_ts=None, to_ts=None) -> tuple[str, dict]:
    where = ["project_slug = :slug"]
    params: dict[str, Any] = {"slug": project_slug}
    if target_scope:
        where.append("target_scope_id = :tgt")
        params["tgt"] = target_scope
    if viewer_user_id is not None:
        where.append("viewer_user_id = :vuid")
        params["vuid"] = int(viewer_user_id)
    if from_ts:
        where.append("created_at >= :from_ts")
        params["from_ts"] = from_ts
    if to_ts:
        where.append("created_at <= :to_ts")
        params["to_ts"] = to_ts
    return " AND ".join(where), params


def _engine():
    return create_engine(db_url, poolclass=NullPool)


def query_audit(project_slug: str, target_scope: str | None = None,
                viewer_user_id: int | None = None,
                from_ts: Any = None, to_ts: Any = None,
                limit: int = 200, offset: int = 0) -> list[dict]:
    where_sql, params = _build_filters(project_slug, target_scope, viewer_user_id, from_ts, to_ts)
    params["limit"] = max(1, min(int(limit), 5000))
    params["offset"] = max(0, int(offset))
    eng = _engine()
    try:
        with eng.connect() as conn:
            rows = conn.execute(text(f"""
                SELECT id, project_slug, viewer_user_id, viewer_scope_id, target_scope_id,
                       intent, policy_version, sql_excerpt, fields_downgraded, row_count, created_at
                FROM public.dash_visibility_read_log
                WHERE {where_sql}
                ORDER BY created_at DESC
                LIMIT :limit OFFSET :offset
            """), params).fetchall()
    finally:
        eng.dispose()
    out = []
    for r in rows:
        out.append({
            "id": r[0], "project_slug": r[1], "viewer_user_id": r[2],
            "viewer_scope_id": r[3], "target_scope_id": r[4], "intent": r[5],
            "policy_version": r[6], "sql_excerpt": r[7],
            "fields_downgraded": list(r[8]) if r[8] else [],
            "row_count": r[9],
            "created_at": r[10].isoformat() if r[10] else None,
        })
    return out


def count_audit(project_slug: str, target_scope: str | None = None,
                viewer_user_id: int | None = None,
                from_ts: Any = None, to_ts: Any = None) -> int:
    where_sql, params = _build_filters(project_slug, target_scope, viewer_user_id, from_ts, to_ts)
    eng = _engine()
    try:
        with eng.connect() as conn:
            row = conn.execute(text(
                f"SELECT COUNT(*) FROM public.dash_visibility_read_log WHERE {where_sql}"
            ), params).first()
    finally:
        eng.dispose()
    return int(row[0]) if row else 0


def export_audit_csv(project_slug: str, target_scope: str | None = None,
                     viewer_user_id: int | None = None,
                     from_ts: Any = None, to_ts: Any = None,
                     limit: int = 5000, offset: int = 0) -> str:
    rows = query_audit(project_slug, target_scope, viewer_user_id,
                       from_ts, to_ts, limit=limit, offset=offset)
    buf = io.StringIO()
    fields = ["id", "created_at", "project_slug", "viewer_user_id", "viewer_scope_id",
              "target_scope_id", "intent", "policy_version", "fields_downgraded",
              "row_count", "sql_excerpt"]
    w = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        rr = dict(r)
        if isinstance(rr.get("fields_downgraded"), list):
            rr["fields_downgraded"] = ",".join(rr["fields_downgraded"])
        w.writerow(rr)
    return buf.getvalue()
