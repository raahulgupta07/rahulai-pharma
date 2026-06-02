"""
Google Drive Connector
======================

Connect Google Drive folders to Dash projects.
Downloads files (Excel, PDF, PPTX, DOCX) and processes them
through the existing upload pipeline.

Auth: Google OAuth2 via google-auth + google-auth-oauthlib.
API:  Google Drive API v3 for file browsing + download.

Setup (one-time, admin):
    1. Create project in Google Cloud Console → APIs & Services
    2. Enable Google Drive API
    3. Create OAuth2 credentials (Web application)
    4. Add redirect URI: https://your-domain/api/gdrive/callback
    5. Set env vars: GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET
"""

import json
import logging
import os
import tempfile
import threading
import time
from os import getenv
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy import create_engine as _sa_create_engine, text
from sqlalchemy.pool import NullPool

from db import db_url
from dash.utils import safe_dumps

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/gdrive", tags=["Google Drive"])

_engine = _sa_create_engine(db_url, poolclass=NullPool)

# ---------------------------------------------------------------------------
# Config from environment
# ---------------------------------------------------------------------------
GOOGLE_CLIENT_ID = getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = getenv("GOOGLE_CLIENT_SECRET", "")
GDRIVE_REDIRECT_PATH = "/api/gdrive/callback"

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

# Supported file extensions for sync
SYNC_EXTENSIONS = {".xlsx", ".xls", ".csv", ".pdf", ".pptx", ".docx", ".txt", ".md", ".jpg", ".jpeg", ".png"}

# Google Workspace MIME types → export formats
WORKSPACE_EXPORTS = {
    "application/vnd.google-apps.spreadsheet": ("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    "application/vnd.google-apps.document": ("docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    "application/vnd.google-apps.presentation": ("pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
}


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class ConnectRequest(BaseModel):
    """Start OAuth2 flow — frontend redirects user to the returned URL."""
    project_slug: str
    redirect_uri: str  # Where to redirect after auth (frontend URL)


class SyncRequest(BaseModel):
    """Sync files from a connected Google Drive source."""
    source_id: int
    force: bool = False  # Re-download all files, not just changed


class SourceCreateRequest(BaseModel):
    """Create a data source after browsing Google Drive."""
    project_slug: str
    folder_id: str
    folder_name: str
    file_types: list[str] = ["xlsx", "pdf", "pptx", "docx"]
    sync_schedule: str = "manual"  # manual | daily | hourly


class AdminConfigRequest(BaseModel):
    client_id: str
    client_secret: str = ""


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

def init_gdrive():
    """Initialize Google Drive connector. Reuses dash_data_sources table."""
    try:
        # Table created by sharepoint.py — just verify it exists
        with _engine.connect() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS public.dash_data_sources (
                    id SERIAL PRIMARY KEY,
                    project_slug TEXT NOT NULL,
                    user_id INTEGER NOT NULL,
                    source_type TEXT NOT NULL DEFAULT 'sharepoint',
                    site_id TEXT,
                    site_name TEXT,
                    drive_id TEXT,
                    folder_path TEXT,
                    folder_id TEXT,
                    file_types JSONB DEFAULT '["xlsx","pdf","pptx","docx"]',
                    sync_schedule TEXT DEFAULT 'manual',
                    sync_state JSONB DEFAULT '{}',
                    last_sync_at TIMESTAMPTZ,
                    status TEXT DEFAULT 'active',
                    access_token TEXT,
                    refresh_token TEXT,
                    token_expires_at BIGINT DEFAULT 0,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
            """))
            conn.commit()
    except Exception as e:
        logger.warning(f"Google Drive init skipped: {e}")


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------

def _build_flow(redirect_uri: str):
    """Create Google OAuth2 flow."""
    try:
        from google_auth_oauthlib.flow import Flow
    except ImportError:
        raise HTTPException(500, "google-auth-oauthlib not installed. Run: pip install google-auth-oauthlib google-api-python-client")

    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise HTTPException(500, "Google Drive not configured. Set GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET env vars.")

    client_config = {
        "web": {
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri],
        }
    }
    flow = Flow.from_client_config(client_config, scopes=SCOPES + ["openid"])
    flow.redirect_uri = redirect_uri
    return flow


def _refresh_token_if_needed(source_id: int) -> str:
    """Get valid access token, refreshing if expired. Returns access_token."""
    with _engine.connect() as conn:
        row = conn.execute(text(
            "SELECT access_token, refresh_token, token_expires_at FROM public.dash_data_sources WHERE id = :id"
        ), {"id": source_id}).fetchone()

    if not row or not row[0]:
        raise HTTPException(401, "Google Drive not connected. Please reconnect.")

    access_token, refresh_token, expires_at = row[0], row[1], row[2] or 0

    # If token still valid (with 5 min buffer), return it
    if time.time() < expires_at - 300:
        return access_token

    # Refresh the token
    if not refresh_token:
        raise HTTPException(401, "Google Drive session expired. Please reconnect.")

    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request as GoogleRequest

        creds = Credentials(
            token=access_token,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=GOOGLE_CLIENT_ID,
            client_secret=GOOGLE_CLIENT_SECRET,
        )
        creds.refresh(GoogleRequest())

        new_access = creds.token
        new_expires = int(time.time()) + 3600
    except Exception as e:
        logger.error(f"Token refresh failed: {e}")
        raise HTTPException(401, "Google Drive session expired. Please reconnect.")

    with _engine.connect() as conn:
        conn.execute(text(
            "UPDATE public.dash_data_sources SET access_token = :at, "
            "token_expires_at = :exp, updated_at = NOW() WHERE id = :id"
        ), {"at": new_access, "exp": new_expires, "id": source_id})
        conn.commit()

    return new_access


def _get_drive_service(access_token: str):
    """Build Google Drive API v3 service from access token."""
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds = Credentials(token=access_token)
    return build("drive", "v3", credentials=creds, cache_discovery=False)


# ---------------------------------------------------------------------------
# OAuth2 Endpoints
# ---------------------------------------------------------------------------

@router.get("/status")
def gdrive_status():
    """Check if Google Drive integration is configured."""
    configured = bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)
    return {"configured": configured}


@router.post("/auth-url")
def get_auth_url(req: ConnectRequest, request: Request):
    """Generate Google OAuth2 login URL."""
    from app.auth import check_project_permission
    user = getattr(getattr(request, 'state', None), 'user', None)
    if not user:
        raise HTTPException(401, "Not authenticated")
    perm = check_project_permission(user, req.project_slug, required_role="editor")
    if not perm:
        raise HTTPException(403, "Editor access required")

    host = request.headers.get("x-forwarded-host", request.headers.get("host", "localhost"))
    scheme = request.headers.get("x-forwarded-proto", "https")
    redirect_uri = f"{scheme}://{host}{GDRIVE_REDIRECT_PATH}"

    flow = _build_flow(redirect_uri)
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state=json.dumps({"project_slug": req.project_slug, "frontend_redirect": req.redirect_uri}),
    )

    return {"auth_url": auth_url}


@router.get("/callback")
def oauth_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    """Google OAuth2 callback — exchanges code for tokens."""
    if error:
        logger.error(f"OAuth error: {error}")
        return RedirectResponse(url="/ui/projects?error=gdrive_auth_failed")

    if not code or not state:
        raise HTTPException(400, "Missing code or state")

    try:
        state_data = json.loads(state)
    except Exception:
        raise HTTPException(400, "Invalid state parameter")

    project_slug = state_data.get("project_slug", "")
    frontend_redirect = state_data.get("frontend_redirect", f"/ui/project/{project_slug}/settings")

    host = request.headers.get("x-forwarded-host", request.headers.get("host", "localhost"))
    scheme = request.headers.get("x-forwarded-proto", "https")
    redirect_uri = f"{scheme}://{host}{GDRIVE_REDIRECT_PATH}"

    flow = _build_flow(redirect_uri)
    try:
        flow.fetch_token(code=code)
    except Exception as e:
        logger.error(f"Token exchange failed: {e}")
        return RedirectResponse(url=f"{frontend_redirect}?error=token_exchange_failed")

    creds = flow.credentials
    access_token = creds.token
    refresh_token = creds.refresh_token or ""
    expires_in = int((creds.expiry - __import__("datetime").datetime.utcnow()).total_seconds()) if creds.expiry else 3600

    # Store tokens in a pending record
    with _engine.connect() as conn:
        row = conn.execute(text(
            "SELECT user_id FROM public.dash_projects WHERE slug = :s"
        ), {"s": project_slug}).fetchone()
        user_id = row[0] if row else 0

        conn.execute(text(
            "INSERT INTO public.dash_data_sources (project_slug, user_id, source_type, "
            "access_token, refresh_token, token_expires_at, status) "
            "VALUES (:slug, :uid, 'gdrive', :at, :rt, :exp, 'pending')"
        ), {
            "slug": project_slug, "uid": user_id,
            "at": access_token, "rt": refresh_token,
            "exp": int(time.time()) + expires_in,
        })
        conn.commit()

    sep = "&" if "?" in frontend_redirect else "?"
    return RedirectResponse(url=f"{frontend_redirect}{sep}gdrive=connected")


# ---------------------------------------------------------------------------
# Browse Google Drive
# ---------------------------------------------------------------------------

@router.get("/browse")
def browse_folder(request: Request, project: str = "", folder_id: str = "root"):
    """Browse folders and files in Google Drive."""
    user = _get_user(request)
    source_id = _get_pending_source(project, user["user_id"])
    token = _refresh_token_if_needed(source_id)
    service = _get_drive_service(token)

    query = f"'{folder_id}' in parents and trashed = false"
    results = service.files().list(
        q=query, pageSize=200,
        fields="files(id,name,mimeType,size,modifiedTime)",
        orderBy="folder,name",
    ).execute()

    folders = []
    files = []
    for item in results.get("files", []):
        mime = item.get("mimeType", "")
        if mime == "application/vnd.google-apps.folder":
            folders.append({"id": item["id"], "name": item["name"]})
        else:
            # Determine extension
            if mime in WORKSPACE_EXPORTS:
                ext = f".{WORKSPACE_EXPORTS[mime][0]}"
            else:
                ext = Path(item["name"]).suffix.lower()
            files.append({
                "id": item["id"],
                "name": item["name"],
                "size": int(item.get("size", 0)),
                "modified": item.get("modifiedTime", ""),
                "mime": mime,
                "ext": ext,
                "supported": ext in SYNC_EXTENSIONS or mime in WORKSPACE_EXPORTS,
            })

    type_counts = {}
    for f in files:
        if f["supported"]:
            t = f["ext"].lstrip(".")
            type_counts[t] = type_counts.get(t, 0) + 1

    return {
        "folders": sorted(folders, key=lambda x: x["name"]),
        "files": sorted(files, key=lambda x: x["name"]),
        "type_counts": type_counts,
        "total_supported": sum(type_counts.values()),
    }


@router.post("/connect")
def finalize_connection(req: SourceCreateRequest, request: Request):
    """Finalize Google Drive connection — save folder config to the pending source."""
    user = _get_user(request)
    source_id = _get_pending_source(req.project_slug, user["user_id"])

    with _engine.connect() as conn:
        conn.execute(text(
            "UPDATE public.dash_data_sources SET "
            "folder_id = :folder_id, folder_path = :folder_name, "
            "site_name = :folder_name, "
            "file_types = :file_types, sync_schedule = :sync_schedule, "
            "status = 'active', updated_at = NOW() "
            "WHERE id = :id"
        ), {
            "folder_id": req.folder_id, "folder_name": req.folder_name,
            "file_types": json.dumps(req.file_types),
            "sync_schedule": req.sync_schedule,
            "id": source_id,
        })
        conn.commit()

    return {"status": "connected", "source_id": source_id}


# ---------------------------------------------------------------------------
# List / Delete Sources
# ---------------------------------------------------------------------------

@router.get("/sources")
def list_sources(request: Request, project: str = ""):
    """List all Google Drive sources for a project."""
    user = _get_user(request)

    with _engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, site_name, folder_path, file_types, sync_schedule, last_sync_at, status, "
            "sync_state, created_at FROM public.dash_data_sources "
            "WHERE project_slug = :slug AND user_id = :uid AND source_type = 'gdrive' "
            "AND status != 'deleted' ORDER BY created_at DESC"
        ), {"slug": project, "uid": user["user_id"]}).fetchall()

    sources = []
    for r in rows:
        sync_state = r[7] if isinstance(r[7], dict) else json.loads(r[7]) if r[7] else {}
        sources.append({
            "id": r[0], "folder_name": r[1], "folder_path": r[2],
            "file_types": r[3] if isinstance(r[3], list) else json.loads(r[3]) if r[3] else [],
            "sync_schedule": r[4], "last_sync_at": str(r[5]) if r[5] else None,
            "status": r[6],
            "files_synced": len(sync_state.get("files", {})),
            "last_error": sync_state.get("last_error", ""),
            "created_at": str(r[8]) if r[8] else None,
        })

    return {"sources": sources}


@router.delete("/sources/{source_id}")
def delete_source(source_id: int, request: Request):
    """Disconnect a Google Drive source."""
    user = _get_user(request)

    with _engine.connect() as conn:
        conn.execute(text(
            "UPDATE public.dash_data_sources SET status = 'deleted', updated_at = NOW() "
            "WHERE id = :id AND user_id = :uid"
        ), {"id": source_id, "uid": user["user_id"]})
        conn.commit()

    return {"status": "deleted"}


_VALID_SCHEDULES = {"manual", "hourly", "daily", "weekly"}


class ScheduleUpdateRequest(BaseModel):
    sync_schedule: str


@router.post("/sources/{source_id}/schedule")
def update_source_schedule(source_id: int, req: ScheduleUpdateRequest, request: Request):
    """Update the sync_schedule for a Google Drive source."""
    from app.auth import check_project_permission
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
        if not check_project_permission(user, row[0], required_role="editor"):
            raise HTTPException(403, "Editor access required")

        conn.execute(text(
            "UPDATE public.dash_data_sources SET sync_schedule = :s, updated_at = NOW() "
            "WHERE id = :id"
        ), {"s": sched, "id": source_id})
        conn.commit()

    return {"ok": True, "source_id": source_id, "sync_schedule": sched}


# ---------------------------------------------------------------------------
# Sync Files
# ---------------------------------------------------------------------------

@router.post("/sync")
def sync_files(req: SyncRequest, request: Request):
    """Sync files from Google Drive to Dash. Returns SSE stream of progress."""
    from starlette.responses import StreamingResponse
    import queue

    user = _get_user(request)

    with _engine.connect() as conn:
        row = conn.execute(text(
            "SELECT id, project_slug, folder_id, file_types, sync_state "
            "FROM public.dash_data_sources WHERE id = :id AND user_id = :uid AND status = 'active'"
        ), {"id": req.source_id, "uid": user["user_id"]}).fetchone()

    if not row:
        raise HTTPException(404, "Source not found or not active")

    source_id, project_slug, folder_id, file_types_raw, sync_state_raw = row
    file_types = file_types_raw if isinstance(file_types_raw, list) else json.loads(file_types_raw) if file_types_raw else []
    sync_state = sync_state_raw if isinstance(sync_state_raw, dict) else json.loads(sync_state_raw) if sync_state_raw else {}
    synced_files = sync_state.get("files", {})

    accept = request.headers.get("accept", "")
    wants_stream = "text/event-stream" in accept

    progress_q: queue.Queue = queue.Queue()

    def _emit(step: str, detail: str):
        progress_q.put({"step": step, "detail": detail})

    def _sync_worker():
        try:
            token = _refresh_token_if_needed(source_id)
            service = _get_drive_service(token)

            all_files = _list_files_recursive(service, folder_id, file_types)
            _emit("Scanning", f"Found {len(all_files)} supported files")

            to_download = []
            for f in all_files:
                key = f["id"]
                prev = synced_files.get(key, {})
                if req.force or prev.get("modified") != f["modified"]:
                    to_download.append(f)

            _emit("Comparing", f"{len(to_download)} new/changed files (of {len(all_files)} total)")

            if not to_download:
                _emit("Complete", "Everything is up to date")
                return

            results = {"success": 0, "failed": 0, "tables": 0, "docs": 0}
            for i, f in enumerate(to_download):
                fname = f["name"]
                _emit(f"Downloading ({i+1}/{len(to_download)})", fname)

                try:
                    file_bytes, actual_name = _download_file(service, f)
                    _emit(f"Processing ({i+1}/{len(to_download)})", f"{actual_name} ({len(file_bytes):,} bytes)")

                    ext = Path(actual_name).suffix.lower()
                    r = _process_gdrive_file(file_bytes, ext, project_slug, actual_name, _emit)

                    tables_count = len(r.get("tables", []))
                    text_len = len(r.get("text", ""))
                    results["tables"] += tables_count
                    if text_len > 0:
                        results["docs"] += 1
                    results["success"] += 1

                    synced_files[f["id"]] = {
                        "name": actual_name, "modified": f["modified"],
                        "size": f.get("size", 0), "synced_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "tables": tables_count, "text_chars": text_len,
                    }

                    _emit(f"Done ({i+1}/{len(to_download)})", f"{actual_name}: {tables_count} tables, {text_len:,} chars")

                except Exception as e:
                    logger.error(f"Failed to process {fname}: {e}")
                    results["failed"] += 1
                    _emit(f"Error ({i+1}/{len(to_download)})", f"{fname}: {str(e)[:150]}")

            new_state = {"files": synced_files, "last_error": ""}
            with _engine.connect() as conn:
                conn.execute(text(
                    "UPDATE public.dash_data_sources SET sync_state = :state, "
                    "last_sync_at = NOW(), updated_at = NOW() WHERE id = :id"
                ), {"state": json.dumps(new_state), "id": source_id})
                conn.commit()

            _emit("Complete", f"{results['success']} files synced ({results['tables']} tables, {results['docs']} docs). {results['failed']} failed.")

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


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_user(request: Request) -> dict:
    user = getattr(getattr(request, 'state', None), 'user', None)
    if not user:
        raise HTTPException(401, "Not authenticated")
    return user


def _get_pending_source(project_slug: str, user_id: int) -> int:
    """Get the most recent pending/active source for a project."""
    with _engine.connect() as conn:
        row = conn.execute(text(
            "SELECT id FROM public.dash_data_sources "
            "WHERE project_slug = :slug AND user_id = :uid AND source_type = 'gdrive' "
            "AND status IN ('pending', 'active') ORDER BY created_at DESC LIMIT 1"
        ), {"slug": project_slug, "uid": user_id}).fetchone()

    if not row:
        raise HTTPException(404, "No Google Drive connection found. Please sign in first.")
    return row[0]


def _list_files_recursive(service, folder_id: str, file_types: list[str], depth: int = 0) -> list[dict]:
    """Recursively list all supported files in a Google Drive folder."""
    if depth > 5:
        return []

    query = f"'{folder_id}' in parents and trashed = false"
    results = service.files().list(
        q=query, pageSize=200,
        fields="files(id,name,mimeType,size,modifiedTime)",
    ).execute()

    files = []
    for item in results.get("files", []):
        mime = item.get("mimeType", "")
        if mime == "application/vnd.google-apps.folder":
            files.extend(_list_files_recursive(service, item["id"], file_types, depth + 1))
        elif mime in WORKSPACE_EXPORTS:
            ext = WORKSPACE_EXPORTS[mime][0]
            if ext in file_types:
                files.append({
                    "id": item["id"], "name": item["name"],
                    "size": int(item.get("size", 0)),
                    "modified": item.get("modifiedTime", ""),
                    "mime": mime, "ext": ext, "workspace": True,
                })
        else:
            ext = Path(item["name"]).suffix.lower().lstrip(".")
            if ext in file_types and f".{ext}" in SYNC_EXTENSIONS:
                files.append({
                    "id": item["id"], "name": item["name"],
                    "size": int(item.get("size", 0)),
                    "modified": item.get("modifiedTime", ""),
                    "mime": mime, "ext": ext, "workspace": False,
                })

    return files


def _download_file(service, file_info: dict) -> tuple[bytes, str]:
    """Download a file from Google Drive. Returns (bytes, filename)."""
    from io import BytesIO
    from googleapiclient.http import MediaIoBaseDownload

    file_id = file_info["id"]
    name = file_info["name"]

    if file_info.get("workspace"):
        # Export Google Workspace file to Office format
        ext, export_mime = WORKSPACE_EXPORTS[file_info["mime"]]
        request = service.files().export_media(fileId=file_id, mimeType=export_mime)
        actual_name = f"{name}.{ext}"
    else:
        request = service.files().get_media(fileId=file_id)
        actual_name = name

    buffer = BytesIO()
    downloader = MediaIoBaseDownload(buffer, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()

    return buffer.getvalue(), actual_name


def _process_gdrive_file(file_bytes: bytes, ext: str, project_slug: str, filename: str, _emit=None) -> dict:
    """Process a downloaded file through the Dash upload pipeline."""
    from app.upload import _conduct_upload

    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    try:
        result = _conduct_upload(tmp_path, ext, project_slug, filename, raw_content=file_bytes, _progress=_emit)

        text_content = result.get("text", "")
        if text_content and project_slug:
            _save_doc_knowledge(project_slug, filename, text_content)

        for tbl in result.get("tables", []):
            _save_table(project_slug, tbl)

        return result
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


def _save_doc_knowledge(project_slug: str, filename: str, text_content: str):
    """Save extracted document text to knowledge directory."""
    from dash.paths import KNOWLEDGE_DIR
    docs_dir = KNOWLEDGE_DIR / project_slug / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)

    safe_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in filename)
    doc_path = docs_dir / f"{safe_name}.txt"

    import tempfile as _tf
    tmp_fd, tmp_path = _tf.mkstemp(dir=str(docs_dir), suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w") as f:
            f.write(text_content)
        os.replace(tmp_path, str(doc_path))
    except Exception:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        raise


def _save_table(project_slug: str, tbl: dict):
    """Save a DataFrame table to the project's PostgreSQL schema."""
    df = tbl.get("df")
    if df is None or df.empty:
        return

    table_name = tbl.get("name", "gdrive_data")

    try:
        from db.session import get_project_engine
        engine = get_project_engine(project_slug)
        schema = project_slug.replace("-", "_").lower()[:63]

        with engine.connect() as conn:
            conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
            conn.commit()

        df.to_sql(table_name, engine, schema=schema, if_exists="replace", index=False)
        logger.info(f"Saved table {schema}.{table_name} ({len(df)} rows)")
    except Exception as e:
        logger.error(f"Failed to save table {table_name}: {e}")


# ---------------------------------------------------------------------------
# Admin Config (Command Center)
# ---------------------------------------------------------------------------

_ENV_FILE = Path(__file__).parent.parent / ".env"


@router.get("/admin/config")
def get_admin_config(request: Request):
    """Get Google Drive config status (admin only)."""
    user = _get_user(request)
    from app.auth import SUPER_ADMIN
    if user.get("username") != SUPER_ADMIN:
        raise HTTPException(403, "Admin only")

    sources = []
    try:
        with _engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT id, project_slug, site_name, folder_path, sync_state, last_sync_at, status "
                "FROM public.dash_data_sources WHERE source_type = 'gdrive' AND status != 'deleted' "
                "ORDER BY created_at DESC"
            )).fetchall()
        for r in rows:
            sync_state = r[4] if isinstance(r[4], dict) else json.loads(r[4]) if r[4] else {}
            sources.append({
                "id": r[0], "project_slug": r[1], "folder_name": r[2],
                "folder_path": r[3], "files_synced": len(sync_state.get("files", {})),
                "last_sync_at": str(r[5]) if r[5] else None, "status": r[6],
            })
    except Exception:
        pass

    return {
        "configured": bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET),
        "client_id": GOOGLE_CLIENT_ID or "",
        "has_secret": bool(GOOGLE_CLIENT_SECRET),
        "sources": sources,
    }


@router.post("/admin/config")
def save_admin_config(req: AdminConfigRequest, request: Request):
    """Save Google Drive config to .env file (admin only)."""
    user = _get_user(request)
    from app.auth import SUPER_ADMIN
    if user.get("username") != SUPER_ADMIN:
        raise HTTPException(403, "Admin only")

    if not req.client_id:
        raise HTTPException(400, "Client ID is required")

    env_lines = []
    if _ENV_FILE.exists():
        env_lines = _ENV_FILE.read_text().splitlines()

    updates = {"GOOGLE_CLIENT_ID": req.client_id}
    if req.client_secret:
        updates["GOOGLE_CLIENT_SECRET"] = req.client_secret

    existing_keys = set()
    new_lines = []
    for line in env_lines:
        key = line.split("=", 1)[0].strip()
        if key in updates:
            new_lines.append(f"{key}={updates[key]}")
            existing_keys.add(key)
        else:
            new_lines.append(line)

    for key, val in updates.items():
        if key not in existing_keys:
            new_lines.append(f"{key}={val}")

    import tempfile as _tf
    env_dir = str(_ENV_FILE.parent)
    fd, tmp_path = _tf.mkstemp(dir=env_dir, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write("\n".join(new_lines) + "\n")
        os.replace(tmp_path, str(_ENV_FILE))
    except Exception:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        raise

    global GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET
    GOOGLE_CLIENT_ID = req.client_id
    if req.client_secret:
        GOOGLE_CLIENT_SECRET = req.client_secret

    return {"status": "saved", "configured": bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)}
