"""
SharePoint Connector
====================

Connect SharePoint document libraries to Dash projects.
Downloads files (Excel, PDF, PPTX, DOCX) and processes them
through the existing upload pipeline.

Auth: Microsoft Entra ID (Azure AD) OAuth2 via MSAL.
API:  Microsoft Graph API for file browsing + download.

Setup (one-time, admin):
    1. Register app in Azure Portal → App Registrations
    2. Add redirect URI: https://your-domain/api/sharepoint/callback
    3. API Permissions: Sites.Read.All, Files.Read.All, User.Read
    4. Set env vars: MS_CLIENT_ID, MS_CLIENT_SECRET, MS_TENANT_ID
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

router = APIRouter(prefix="/api/sharepoint", tags=["SharePoint"])

_engine = _sa_create_engine(db_url, poolclass=NullPool)

# ---------------------------------------------------------------------------
# Config from environment
# ---------------------------------------------------------------------------
MS_CLIENT_ID = getenv("MS_CLIENT_ID", "")
MS_CLIENT_SECRET = getenv("MS_CLIENT_SECRET", "")
MS_TENANT_ID = getenv("MS_TENANT_ID", "")
MS_REDIRECT_PATH = "/api/sharepoint/callback"

GRAPH_BASE = "https://graph.microsoft.com/v1.0"

# Supported file extensions for sync
SYNC_EXTENSIONS = {".xlsx", ".xls", ".csv", ".pdf", ".pptx", ".docx", ".txt", ".md", ".jpg", ".jpeg", ".png"}


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class ConnectRequest(BaseModel):
    """Start OAuth2 flow — frontend redirects user to the returned URL."""
    project_slug: str
    redirect_uri: str  # Where to redirect after auth (frontend URL)


class SyncRequest(BaseModel):
    """Sync files from a connected SharePoint source."""
    source_id: int
    force: bool = False  # Re-download all files, not just changed


class SourceCreateRequest(BaseModel):
    """Create a data source after browsing SharePoint."""
    project_slug: str
    site_id: str
    site_name: str
    drive_id: str
    folder_path: str
    folder_id: str
    file_types: list[str] = ["xlsx", "pdf", "pptx", "docx"]
    sync_schedule: str = "manual"  # manual | daily | hourly


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

def _bootstrap_sharepoint_tables():
    """Create data_sources table if not exists."""
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


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------

def _get_msal_app():
    """Create MSAL ConfidentialClientApplication."""
    try:
        import msal
    except ImportError:
        raise HTTPException(500, "msal package not installed. Run: pip install msal")

    if not MS_CLIENT_ID or not MS_CLIENT_SECRET or not MS_TENANT_ID:
        raise HTTPException(500, "SharePoint not configured. Set MS_CLIENT_ID, MS_CLIENT_SECRET, MS_TENANT_ID env vars.")

    return msal.ConfidentialClientApplication(
        MS_CLIENT_ID,
        authority=f"https://login.microsoftonline.com/{MS_TENANT_ID}",
        client_credential=MS_CLIENT_SECRET,
    )


def _refresh_token_if_needed(source_id: int) -> str:
    """Get valid access token, refreshing if expired. Returns access_token."""
    with _engine.connect() as conn:
        row = conn.execute(text(
            "SELECT access_token, refresh_token, token_expires_at FROM public.dash_data_sources WHERE id = :id"
        ), {"id": source_id}).fetchone()

    if not row or not row[0]:
        raise HTTPException(401, "SharePoint not connected. Please reconnect.")

    access_token, refresh_token, expires_at = row[0], row[1], row[2] or 0

    # If token still valid (with 5 min buffer), return it
    if time.time() < expires_at - 300:
        return access_token

    # Refresh the token
    if not refresh_token:
        raise HTTPException(401, "SharePoint session expired. Please reconnect.")

    app = _get_msal_app()
    result = app.acquire_token_by_refresh_token(
        refresh_token,
        scopes=["https://graph.microsoft.com/.default"],
    )

    if "access_token" not in result:
        logger.error(f"Token refresh failed: {result.get('error_description', 'unknown')}")
        raise HTTPException(401, "SharePoint session expired. Please reconnect.")

    new_access = result["access_token"]
    new_refresh = result.get("refresh_token", refresh_token)
    new_expires = int(time.time()) + result.get("expires_in", 3600)

    with _engine.connect() as conn:
        conn.execute(text(
            "UPDATE public.dash_data_sources SET access_token = :at, refresh_token = :rt, "
            "token_expires_at = :exp, updated_at = NOW() WHERE id = :id"
        ), {"at": new_access, "rt": new_refresh, "exp": new_expires, "id": source_id})
        conn.commit()

    return new_access


def _graph_get(access_token: str, endpoint: str, params: dict | None = None) -> dict:
    """Make authenticated GET request to Microsoft Graph API."""
    import httpx
    url = f"{GRAPH_BASE}{endpoint}" if endpoint.startswith("/") else endpoint
    resp = httpx.get(
        url,
        headers={"Authorization": f"Bearer {access_token}"},
        params=params,
        timeout=30,
    )
    if resp.status_code == 401:
        raise HTTPException(401, "SharePoint token expired. Please reconnect.")
    if resp.status_code != 200:
        logger.error(f"Graph API error {resp.status_code}: {resp.text[:300]}")
        raise HTTPException(502, f"SharePoint API error: {resp.status_code}")
    return resp.json()


# ---------------------------------------------------------------------------
# OAuth2 Endpoints
# ---------------------------------------------------------------------------

@router.get("/status")
def sharepoint_status():
    """Check if SharePoint integration is configured."""
    configured = bool(MS_CLIENT_ID and MS_CLIENT_SECRET and MS_TENANT_ID)
    return {"configured": configured}


@router.post("/auth-url")
def get_auth_url(req: ConnectRequest, request: Request):
    """Generate Microsoft OAuth2 login URL."""
    from app.auth import check_project_permission
    user = getattr(getattr(request, 'state', None), 'user', None)
    if not user:
        raise HTTPException(401, "Not authenticated")
    perm = check_project_permission(user, req.project_slug, required_role="editor")
    if not perm:
        raise HTTPException(403, "Editor access required")

    app = _get_msal_app()

    # Build redirect URI from request host
    host = request.headers.get("x-forwarded-host", request.headers.get("host", "localhost"))
    scheme = request.headers.get("x-forwarded-proto", "https")
    redirect_uri = f"{scheme}://{host}{MS_REDIRECT_PATH}"

    auth_url = app.get_authorization_request_url(
        scopes=["Sites.Read.All", "Files.Read.All", "User.Read", "offline_access"],
        redirect_uri=redirect_uri,
        state=json.dumps({"project_slug": req.project_slug, "frontend_redirect": req.redirect_uri}),
    )

    return {"auth_url": auth_url}


@router.get("/callback")
def oauth_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    """Microsoft OAuth2 callback — exchanges code for tokens."""
    if error:
        logger.error(f"OAuth error: {error}")
        return RedirectResponse(url="/ui/projects?error=sharepoint_auth_failed")

    if not code or not state:
        raise HTTPException(400, "Missing code or state")

    try:
        state_data = json.loads(state)
    except Exception:
        raise HTTPException(400, "Invalid state parameter")

    project_slug = state_data.get("project_slug", "")
    frontend_redirect = state_data.get("frontend_redirect", f"/ui/project/{project_slug}/settings")

    # Exchange code for tokens
    app = _get_msal_app()
    host = request.headers.get("x-forwarded-host", request.headers.get("host", "localhost"))
    scheme = request.headers.get("x-forwarded-proto", "https")
    redirect_uri = f"{scheme}://{host}{MS_REDIRECT_PATH}"

    result = app.acquire_token_by_authorization_code(
        code,
        scopes=["Sites.Read.All", "Files.Read.All", "User.Read", "offline_access"],
        redirect_uri=redirect_uri,
    )

    if "access_token" not in result:
        logger.error(f"Token exchange failed: {result.get('error_description', 'unknown')}")
        return RedirectResponse(url=f"{frontend_redirect}?error=token_exchange_failed")

    # Get user info from the request — the callback comes from Microsoft redirect,
    # so we need to extract project ownership from state
    # Store tokens temporarily in session-like storage keyed by project_slug
    access_token = result["access_token"]
    refresh_token = result.get("refresh_token", "")
    expires_in = result.get("expires_in", 3600)

    # Store in a temp record so the frontend can complete the connection
    with _engine.connect() as conn:
        # Find user_id from project ownership
        row = conn.execute(text(
            "SELECT user_id FROM public.dash_projects WHERE slug = :s"
        ), {"s": project_slug}).fetchone()
        user_id = row[0] if row else 0

        conn.execute(text(
            "INSERT INTO public.dash_data_sources (project_slug, user_id, source_type, "
            "access_token, refresh_token, token_expires_at, status) "
            "VALUES (:slug, :uid, 'sharepoint', :at, :rt, :exp, 'pending')"
        ), {
            "slug": project_slug, "uid": user_id,
            "at": access_token, "rt": refresh_token,
            "exp": int(time.time()) + expires_in,
        })
        conn.commit()

    # Redirect back to frontend with success flag
    sep = "&" if "?" in frontend_redirect else "?"
    return RedirectResponse(url=f"{frontend_redirect}{sep}sharepoint=connected")


# ---------------------------------------------------------------------------
# Browse SharePoint
# ---------------------------------------------------------------------------

@router.get("/sites")
def list_sites(request: Request, project: str = ""):
    """List SharePoint sites accessible to the user."""
    user = _get_user(request)
    source_id = _get_pending_source(project, user["user_id"])
    token = _refresh_token_if_needed(source_id)

    data = _graph_get(token, "/sites?search=*&$top=50&$select=id,displayName,webUrl")
    sites = []
    for s in data.get("value", []):
        sites.append({
            "id": s.get("id", ""),
            "name": s.get("displayName", "Unknown"),
            "url": s.get("webUrl", ""),
        })

    return {"sites": sites, "source_id": source_id}


@router.get("/drives")
def list_drives(request: Request, project: str = "", site_id: str = ""):
    """List document libraries (drives) for a SharePoint site."""
    user = _get_user(request)
    source_id = _get_pending_source(project, user["user_id"])
    token = _refresh_token_if_needed(source_id)

    if not site_id:
        raise HTTPException(400, "site_id required")

    data = _graph_get(token, f"/sites/{site_id}/drives?$select=id,name,driveType,webUrl")
    drives = []
    for d in data.get("value", []):
        drives.append({
            "id": d.get("id", ""),
            "name": d.get("name", "Unknown"),
            "type": d.get("driveType", ""),
            "url": d.get("webUrl", ""),
        })

    return {"drives": drives}


@router.get("/browse")
def browse_folder(request: Request, project: str = "", drive_id: str = "", folder_id: str = "root"):
    """Browse folders and files in a SharePoint drive."""
    user = _get_user(request)
    source_id = _get_pending_source(project, user["user_id"])
    token = _refresh_token_if_needed(source_id)

    if not drive_id:
        raise HTTPException(400, "drive_id required")

    # Get children of folder
    endpoint = f"/drives/{drive_id}/items/{folder_id}/children?$top=200&$select=id,name,size,file,folder,lastModifiedDateTime,webUrl"
    data = _graph_get(token, endpoint)

    folders = []
    files = []
    for item in data.get("value", []):
        if "folder" in item:
            folders.append({
                "id": item["id"],
                "name": item["name"],
                "child_count": item["folder"].get("childCount", 0),
            })
        elif "file" in item:
            ext = Path(item["name"]).suffix.lower()
            files.append({
                "id": item["id"],
                "name": item["name"],
                "size": item.get("size", 0),
                "modified": item.get("lastModifiedDateTime", ""),
                "mime": item["file"].get("mimeType", ""),
                "ext": ext,
                "supported": ext in SYNC_EXTENSIONS,
            })

    # Count supported files by type
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
    """Finalize SharePoint connection — save site/folder config to the pending source."""
    user = _get_user(request)
    source_id = _get_pending_source(req.project_slug, user["user_id"])

    with _engine.connect() as conn:
        conn.execute(text(
            "UPDATE public.dash_data_sources SET "
            "site_id = :site_id, site_name = :site_name, drive_id = :drive_id, "
            "folder_path = :folder_path, folder_id = :folder_id, "
            "file_types = :file_types, sync_schedule = :sync_schedule, "
            "status = 'active', updated_at = NOW() "
            "WHERE id = :id"
        ), {
            "site_id": req.site_id, "site_name": req.site_name,
            "drive_id": req.drive_id, "folder_path": req.folder_path,
            "folder_id": req.folder_id,
            "file_types": json.dumps(req.file_types),
            "sync_schedule": req.sync_schedule,
            "id": source_id,
        })
        conn.commit()

    return {"status": "connected", "source_id": source_id}


# ---------------------------------------------------------------------------
# List Sources
# ---------------------------------------------------------------------------

@router.get("/sources")
def list_sources(request: Request, project: str = ""):
    """List all SharePoint sources for a project."""
    user = _get_user(request)

    with _engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, site_name, folder_path, file_types, sync_schedule, last_sync_at, status, "
            "sync_state, created_at FROM public.dash_data_sources "
            "WHERE project_slug = :slug AND user_id = :uid AND source_type = 'sharepoint' "
            "AND status != 'deleted' ORDER BY created_at DESC"
        ), {"slug": project, "uid": user["user_id"]}).fetchall()

    sources = []
    for r in rows:
        sync_state = r[7] if isinstance(r[7], dict) else json.loads(r[7]) if r[7] else {}
        sources.append({
            "id": r[0], "site_name": r[1], "folder_path": r[2],
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
    """Disconnect a SharePoint source."""
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
    """Update the sync_schedule for a SharePoint source."""
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
    """Sync files from SharePoint to Dash. Returns SSE stream of progress."""
    from starlette.responses import StreamingResponse
    import queue

    user = _get_user(request)

    # Load source config
    with _engine.connect() as conn:
        row = conn.execute(text(
            "SELECT id, project_slug, drive_id, folder_id, file_types, sync_state "
            "FROM public.dash_data_sources WHERE id = :id AND user_id = :uid AND status = 'active'"
        ), {"id": req.source_id, "uid": user["user_id"]}).fetchone()

    if not row:
        raise HTTPException(404, "Source not found or not active")

    source_id, project_slug, drive_id, folder_id, file_types_raw, sync_state_raw = row
    file_types = file_types_raw if isinstance(file_types_raw, list) else json.loads(file_types_raw) if file_types_raw else []
    sync_state = sync_state_raw if isinstance(sync_state_raw, dict) else json.loads(sync_state_raw) if sync_state_raw else {}
    synced_files = sync_state.get("files", {})

    # Check if client wants streaming
    accept = request.headers.get("accept", "")
    wants_stream = "text/event-stream" in accept

    progress_q: queue.Queue = queue.Queue()

    def _emit(step: str, detail: str):
        progress_q.put({"step": step, "detail": detail})

    def _sync_worker():
        try:
            token = _refresh_token_if_needed(source_id)

            # List all files in folder (recursive)
            all_files = _list_files_recursive(token, drive_id, folder_id, file_types)
            _emit("Scanning", f"Found {len(all_files)} supported files")

            # Filter to new/changed files
            to_download = []
            for f in all_files:
                key = f["id"]
                prev = synced_files.get(key, {})
                if req.force or prev.get("modified") != f["modified"] or prev.get("size") != f["size"]:
                    to_download.append(f)

            _emit("Comparing", f"{len(to_download)} new/changed files (of {len(all_files)} total)")

            if not to_download:
                _emit("Complete", "Everything is up to date")
                return

            # Download and process each file
            results = {"success": 0, "failed": 0, "tables": 0, "docs": 0}
            for i, f in enumerate(to_download):
                fname = f["name"]
                _emit(f"Downloading ({i+1}/{len(to_download)})", fname)

                try:
                    file_bytes = _download_file(token, drive_id, f["id"])
                    _emit(f"Processing ({i+1}/{len(to_download)})", f"{fname} ({len(file_bytes):,} bytes)")

                    # Process through Dash upload pipeline
                    ext = Path(fname).suffix.lower()
                    r = _process_sharepoint_file(file_bytes, ext, project_slug, fname, _emit)

                    # Track results
                    tables_count = len(r.get("tables", []))
                    text_len = len(r.get("text", ""))
                    results["tables"] += tables_count
                    if text_len > 0:
                        results["docs"] += 1
                    results["success"] += 1

                    # Update sync state
                    synced_files[f["id"]] = {
                        "name": fname, "modified": f["modified"],
                        "size": f["size"], "synced_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "tables": tables_count, "text_chars": text_len,
                    }

                    _emit(f"Done ({i+1}/{len(to_download)})", f"{fname}: {tables_count} tables, {text_len:,} chars")

                except Exception as e:
                    logger.error(f"Failed to process {fname}: {e}")
                    results["failed"] += 1
                    _emit(f"Error ({i+1}/{len(to_download)})", f"{fname}: {str(e)[:150]}")

            # Save sync state
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

            # Save error in sync state
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
        # Wait for sync to complete
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
    """Get the most recent pending/active source for a project. Used during setup flow."""
    with _engine.connect() as conn:
        row = conn.execute(text(
            "SELECT id FROM public.dash_data_sources "
            "WHERE project_slug = :slug AND user_id = :uid AND source_type = 'sharepoint' "
            "AND status IN ('pending', 'active') ORDER BY created_at DESC LIMIT 1"
        ), {"slug": project_slug, "uid": user_id}).fetchone()

    if not row:
        raise HTTPException(404, "No SharePoint connection found. Please sign in first.")
    return row[0]


def _list_files_recursive(token: str, drive_id: str, folder_id: str, file_types: list[str], depth: int = 0) -> list[dict]:
    """Recursively list all supported files in a SharePoint folder."""
    if depth > 5:  # Prevent infinite recursion
        return []

    endpoint = f"/drives/{drive_id}/items/{folder_id}/children?$top=200&$select=id,name,size,file,folder,lastModifiedDateTime"
    data = _graph_get(token, endpoint)

    files = []
    for item in data.get("value", []):
        if "folder" in item:
            # Recurse into subfolders
            files.extend(_list_files_recursive(token, drive_id, item["id"], file_types, depth + 1))
        elif "file" in item:
            ext = Path(item["name"]).suffix.lower().lstrip(".")
            if ext in file_types and f".{ext}" in SYNC_EXTENSIONS:
                files.append({
                    "id": item["id"],
                    "name": item["name"],
                    "size": item.get("size", 0),
                    "modified": item.get("lastModifiedDateTime", ""),
                    "ext": ext,
                })

    return files


def _download_file(token: str, drive_id: str, item_id: str) -> bytes:
    """Download a file from SharePoint via Graph API."""
    import httpx
    resp = httpx.get(
        f"{GRAPH_BASE}/drives/{drive_id}/items/{item_id}/content",
        headers={"Authorization": f"Bearer {token}"},
        timeout=120,
        follow_redirects=True,
    )
    if resp.status_code != 200:
        raise Exception(f"Download failed: HTTP {resp.status_code}")
    return resp.content


def _process_sharepoint_file(file_bytes: bytes, ext: str, project_slug: str, filename: str, _emit=None) -> dict:
    """Process a downloaded file through the Dash upload pipeline."""
    from app.upload import _conduct_upload

    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    try:
        result = _conduct_upload(tmp_path, ext, project_slug, filename, raw_content=file_bytes, _progress=_emit)

        # Save text to knowledge
        text_content = result.get("text", "")
        if text_content and project_slug:
            _save_doc_knowledge(project_slug, filename, text_content)

        # Save tables to PostgreSQL
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

    # Atomic write
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

    table_name = tbl.get("name", "sharepoint_data")

    try:
        from db.session import get_project_engine
        engine = get_project_engine(project_slug)
        schema = project_slug.replace("-", "_").lower()[:63]

        # Ensure schema exists
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
    """Get SharePoint config status (admin only)."""
    user = _get_user(request)
    from app.auth import SUPER_ADMIN
    if user.get("username") != SUPER_ADMIN:
        raise HTTPException(403, "Admin only")

    # Load all sources across all projects
    sources = []
    try:
        with _engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT id, project_slug, site_name, folder_path, sync_state, last_sync_at, status "
                "FROM public.dash_data_sources WHERE source_type = 'sharepoint' AND status != 'deleted' "
                "ORDER BY created_at DESC"
            )).fetchall()
        for r in rows:
            sync_state = r[4] if isinstance(r[4], dict) else json.loads(r[4]) if r[4] else {}
            sources.append({
                "id": r[0], "project_slug": r[1], "site_name": r[2],
                "folder_path": r[3], "files_synced": len(sync_state.get("files", {})),
                "last_sync_at": str(r[5]) if r[5] else None, "status": r[6],
            })
    except Exception:
        pass

    return {
        "configured": bool(MS_CLIENT_ID and MS_CLIENT_SECRET and MS_TENANT_ID),
        "client_id": MS_CLIENT_ID or "",
        "tenant_id": MS_TENANT_ID or "",
        "has_secret": bool(MS_CLIENT_SECRET),
        "sources": sources,
    }


class AdminConfigRequest(BaseModel):
    client_id: str
    client_secret: str = ""
    tenant_id: str


@router.post("/admin/config")
def save_admin_config(req: AdminConfigRequest, request: Request):
    """Save SharePoint config to .env file (admin only)."""
    user = _get_user(request)
    from app.auth import SUPER_ADMIN
    if user.get("username") != SUPER_ADMIN:
        raise HTTPException(403, "Admin only")

    if not req.client_id or not req.tenant_id:
        raise HTTPException(400, "Client ID and Tenant ID are required")

    # Read existing .env
    env_lines = []
    if _ENV_FILE.exists():
        env_lines = _ENV_FILE.read_text().splitlines()

    # Update or add MS_ vars
    updates = {"MS_CLIENT_ID": req.client_id, "MS_TENANT_ID": req.tenant_id}
    if req.client_secret:
        updates["MS_CLIENT_SECRET"] = req.client_secret

    existing_keys = set()
    new_lines = []
    for line in env_lines:
        key = line.split("=", 1)[0].strip()
        if key in updates:
            new_lines.append(f"{key}={updates[key]}")
            existing_keys.add(key)
        else:
            new_lines.append(line)

    # Add any missing keys
    for key, val in updates.items():
        if key not in existing_keys:
            new_lines.append(f"{key}={val}")

    # Write .env atomically
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

    # Update in-memory config
    global MS_CLIENT_ID, MS_CLIENT_SECRET, MS_TENANT_ID
    MS_CLIENT_ID = req.client_id
    MS_TENANT_ID = req.tenant_id
    if req.client_secret:
        MS_CLIENT_SECRET = req.client_secret

    return {"status": "saved", "configured": bool(MS_CLIENT_ID and MS_CLIENT_SECRET and MS_TENANT_ID)}


# ---------------------------------------------------------------------------
# Init — called on app startup
# ---------------------------------------------------------------------------

def init_sharepoint():
    """Initialize SharePoint tables. Call during app lifespan."""
    try:
        _bootstrap_sharepoint_tables()
    except Exception as e:
        logger.warning(f"SharePoint init skipped: {e}")
