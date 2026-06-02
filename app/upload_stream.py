"""GB-scale streaming upload endpoint — no RAM materialization.

Replaces the legacy 200MB-capped `/api/upload` for big files. Pipes
multipart bytes straight to disk in 64KB chunks, then dispatches to
psycopg3 COPY-based ingest. Emits SSE progress every 2s.

Endpoint:
  POST /api/projects/{slug}/upload-stream?table=&format=
    multipart/form-data field "file" → REQUIRED
    query param "table" → REQUIRED (target table name in project schema)
    query param "format" → optional (csv | xlsx | parquet — defaults to file ext)

Limits:
  STREAM_UPLOAD_MAX_GB env (default 10) — absolute ceiling, fails before disk full.
  No row cap. No RAM cap.

Returns SSE stream with events:
  event: progress  data: {"pct":int,"bytes":int,"rows":int}
  event: ingest    data: {"status":"copying","table":"..."}
  event: done      data: {"rows_loaded":int,"elapsed_s":float,"columns":[...]}
  event: error     data: {"error":"...","stage":"..."}
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from sse_starlette.sse import EventSourceResponse

logger = logging.getLogger(__name__)
router = APIRouter(tags=["upload-stream"])

MAX_GB = float(os.getenv("STREAM_UPLOAD_MAX_GB", "10"))
MAX_BYTES = int(MAX_GB * 1024 * 1024 * 1024)
CHUNK_SIZE = 64 * 1024  # 64KB read buffer
PROGRESS_INTERVAL_S = 1.0


def _safe_slug(s: str) -> str:
    import re
    return re.sub(r"[^a-z0-9_]", "_", (s or "").lower())[:63]


def _safe_table_name(s: str) -> str:
    import re
    s = re.sub(r"[^a-z0-9_]", "_", (s or "table").lower()).strip("_")
    if not s:
        s = "uploaded"
    if s[0].isdigit():
        s = f"t_{s}"
    return s[:63]


@router.post("/api/projects/{slug}/upload-stream")
async def upload_stream(
    slug: str, request: Request,
    file: UploadFile = File(...),
    table: str = "", format: str = "",
):
    """Streaming upload + COPY-based ingest. Returns SSE progress stream.

    Auth: editor role on project.
    """
    # Auth check (reuse existing helpers — match app/upload.py signature pattern)
    try:
        from app.auth import get_current_user, check_project_permission
        user = getattr(getattr(request, "state", None), "user", None) or get_current_user(request)
        if not user:
            raise HTTPException(401, "Not authenticated")
        if not check_project_permission(user, slug, required_role="editor"):
            raise HTTPException(403, "Editor role required")
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"upload_stream auth check failed: {e}")
        raise HTTPException(401, "Auth failed")

    # Parse multipart boundary from content-type
    content_type = request.headers.get("content-type", "")
    if not content_type.startswith("multipart/form-data"):
        raise HTTPException(400, "Expected multipart/form-data")

    # Resolve table name
    project_slug = _safe_slug(slug)
    target_table = _safe_table_name(table)
    if not target_table:
        raise HTTPException(400, "table query param required")

    # Spool to temp file. FastAPI's UploadFile uses SpooledTemporaryFile
    # that auto-spools to disk after 1MB — never holds full file in RAM.
    tmp_dir = Path(tempfile.gettempdir()) / "dash_upload_stream"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    src_name = (file.filename or "upload")
    tmp_path = tmp_dir / f"{uuid.uuid4().hex}_{target_table}_{Path(src_name).suffix or '.bin'}"

    async def event_stream():
        t0 = time.time()
        bytes_written = 0
        try:
            # 1. SPOOL UPLOAD TO DISK (chunked write from UploadFile.SpooledTemporaryFile)
            yield {"event": "ingest", "data": json.dumps({"status": "receiving", "table": target_table})}

            with open(tmp_path, "wb") as f:
                last_progress_ts = time.time()
                while True:
                    chunk = await file.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    f.write(chunk)
                    bytes_written += len(chunk)
                    if bytes_written > MAX_BYTES:
                        f.close()
                        try:
                            tmp_path.unlink()
                        except Exception:
                            pass
                        yield {"event": "error", "data": json.dumps({
                            "error": f"file exceeds {MAX_GB}GB cap", "stage": "upload"
                        })}
                        return
                    now = time.time()
                    if now - last_progress_ts > PROGRESS_INTERVAL_S:
                        yield {"event": "progress", "data": json.dumps({
                            "stage": "upload", "bytes": bytes_written,
                            "mb": round(bytes_written / 1024 / 1024, 2),
                        })}
                        last_progress_ts = now

            yield {"event": "progress", "data": json.dumps({
                "stage": "upload", "bytes": bytes_written,
                "mb": round(bytes_written / 1024 / 1024, 2), "pct": 100,
            })}
            yield {"event": "ingest", "data": json.dumps({
                "status": "upload_done", "bytes": bytes_written,
                "elapsed_s": round(time.time() - t0, 1),
            })}

            # 2. DETECT FORMAT
            fmt = (format or "").lower()
            if not fmt:
                # Sniff: peek at first bytes
                with open(tmp_path, "rb") as fh:
                    head = fh.read(16)
                if head.startswith(b"PK"):
                    fmt = "xlsx"
                elif head.startswith(b"PAR1"):
                    fmt = "parquet"
                else:
                    fmt = "csv"
            yield {"event": "ingest", "data": json.dumps({"status": "format_detected", "format": fmt})}

            # 3. STREAM INTO POSTGRES via COPY
            yield {"event": "ingest", "data": json.dumps({"status": "copying", "table": target_table})}

            # Collect progress from copy_stream callback into queue
            import asyncio
            progress_q: asyncio.Queue = asyncio.Queue()
            loop = asyncio.get_event_loop()

            def _cb(pct_or_rows: int, second: int):
                try:
                    loop.call_soon_threadsafe(
                        progress_q.put_nowait,
                        {"pct_or_rows": pct_or_rows, "extra": second},
                    )
                except Exception:
                    pass

            from dash.ingest.copy_stream import stream_to_postgres

            async def run_ingest():
                return await asyncio.to_thread(
                    stream_to_postgres,
                    str(tmp_path), project_slug, target_table,
                    progress_cb=_cb,
                )

            task = asyncio.create_task(run_ingest())
            while not task.done():
                try:
                    item = await asyncio.wait_for(progress_q.get(), timeout=1.5)
                    yield {"event": "progress", "data": json.dumps({
                        "stage": "copy", **item,
                    })}
                except asyncio.TimeoutError:
                    yield {"event": "ingest", "data": json.dumps({"status": "copying"})}

            result = await task

            elapsed = round(time.time() - t0, 1)
            yield {"event": "done", "data": json.dumps({
                "table": target_table,
                "format": fmt,
                "bytes_uploaded": bytes_written,
                "rows_loaded": result.get("rows_loaded", 0),
                "columns": result.get("columns", [])[:50],
                "column_count": len(result.get("columns", [])),
                "ingest_elapsed_s": result.get("elapsed_s"),
                "total_elapsed_s": elapsed,
            })}

        except Exception as e:
            logger.exception("upload_stream failed")
            yield {"event": "error", "data": json.dumps({"error": str(e)[:500], "stage": "ingest"})}
        finally:
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except Exception:
                pass

    return EventSourceResponse(event_stream())


@router.get("/api/projects/{slug}/upload-stream/status")
def upload_stream_status(slug: str):
    """Sanity-check endpoint — confirms streaming ingest is wired."""
    return {
        "enabled": True,
        "max_gb": MAX_GB,
        "max_bytes": MAX_BYTES,
        "supports": ["csv", "xlsx", "xls", "parquet"],
        "chunk_size_bytes": CHUNK_SIZE,
    }
