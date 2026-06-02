"""
OneDrive Connector
==================

Connect personal or business OneDrive accounts to Dash projects.
Downloads files (Excel, PDF, PPTX, DOCX, etc.) and processes them
through the existing upload pipeline.

Auth: Microsoft Entra ID (Azure AD) OAuth2 via MSAL.
      `tenant=common` so the same Azure App Registration accepts both
      personal Microsoft accounts and work/school (business) accounts.
API:  Microsoft Graph `/me/drive` — no sites/drives layer to traverse.

Setup (one-time, admin):
    1. Register app in Azure Portal -> App Registrations
       (set "Supported account types" to "Accounts in any organizational
        directory and personal Microsoft accounts").
    2. Add redirect URI: https://your-domain/api/onedrive/callback
    3. Delegated permissions: Files.Read, Files.Read.All, offline_access
    4. Set env vars: MS_CLIENT_ID, MS_CLIENT_SECRET, MS_TENANT_ID=common
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
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import create_engine as _sa_create_engine, text
from sqlalchemy.pool import NullPool

from db import db_url
from dash.utils import safe_dumps

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/onedrive", tags=["onedrive"])

_engine = _sa_create_engine(db_url, poolclass=NullPool)

# ---------------------------------------------------------------------------
# Config from environment
# ---------------------------------------------------------------------------
MS_CLIENT_ID = getenv("MS_CLIENT_ID", "")
MS_CLIENT_SECRET = getenv("MS_CLIENT_SECRET", "")
# Default to "common" so the same Azure App accepts personal + business accounts.
MS_TENANT_ID = getenv("MS_TENANT_ID", "common") or "common"
MS_REDIRECT_PATH = "/api/onedrive/callback"

GRAPH_BASE = "https://graph.microsoft.com/v1.0"

# OneDrive uses a downgraded scope set vs SharePoint. `Files.Read.All` covers
# shared files; if an account can't grant it, MSAL falls back gracefully to
# whatever subset the user/tenant allows.
SCOPES = ["Files.Read", "Files.Read.All", "offline_access"]

SYNC_EXTENSIONS = {".xlsx", ".xls", ".csv", ".pdf", ".pptx", ".docx", ".txt", ".md", ".jpg", ".jpeg", ".png"}


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class ConnectRequest(BaseModel):
    """Start OAuth2 flow."""
    project_slug: str


class SyncRequest(BaseModel):
    """Sync files from a connected OneDrive source."""
    source_id: int
    folder_id: str = "root"


class SourceCreateRequest(BaseModel):
    """Create a OneDrive data source after browsing."""
    project_slug: str
    folder_id: str
    folder_path: str = ""
    file_types: list[str] = ["xlsx", "csv", "pdf", "pptx", "docx"]


class AdminConfigRequest(BaseModel):
    ms_client_id: str
    ms_client_secret: str
    ms_tenant_id: str = "common"


# ---------------------------------------------------------------------------
# Token helpers (MSAL)
# ---------------------------------------------------------------------------

def _get_msal_app():
    """Create MSAL ConfidentialClientApplication."""
    try:
        import msal
    except ImportError:
        raise HTTPException(500, "msal package not installed. Run: pip install msal")

    if not MS_CLIENT_ID or not MS_CLIENT_SECRET or not MS_TENANT_ID:
        raise HTTPException(500, "OneDrive not configured. Set MS_CLIENT_ID, MS_CLIENT_SECRET, MS_TENANT_ID env vars.")

    return msal.ConfidentialClientApplication(
        MS_CLIENT_ID,
        authority=f"https://login.microsoftonline.com/{MS_TENANT_ID}",
        client_credential=MS_CLIENT_SECRET,
    )


def _refresh_token_if_needed(source_id: int) -> str:
    """Get valid access token, refreshing if expired."""
    with _engine.connect() as conn:
        row = conn.execute(text(
            "SELECT access_token, refresh_token, token_expires_at FROM public.dash_data_sources WHERE id = :id"
        ), {"id": source_id}).fetchone()

    if not row or not row[0]:
        raise HTTPException(401, "OneDrive not connected. Please reconnect.")

    access_token, refresh_token, expires_at = row[0], row[1], row[2] or 0

    if time.time() < expires_at - 300:
        return access_token

    if not refresh_token:
        raise HTTPException(401, "OneDrive session expired. Please reconnect.")

    app = _get_msal_app()
    result = app.acquire_token_by_refresh_token(
        refresh_token,
        scopes=["https://graph.microsoft.com/.default"],
    )

    if "access_token" not in result:
        logger.error(f"Token refresh failed: {result.get('error_description', 'unknown')}")
        raise HTTPException(401, "OneDrive session expired. Please reconnect.")

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
    """Authenticated GET against Microsoft Graph."""
    import httpx
    url = f"{GRAPH_BASE}{endpoint}" if endpoint.startswith("/") else endpoint
    resp = httpx.get(
        url,
        headers={"Authorization": f"Bearer {access_token}"},
        params=params,
        timeout=30,
    )
    if resp.status_code == 401:
        raise HTTPException(401, "OneDrive token expired. Please reconnect.")
    if resp.status_code != 200:
        logger.error(f"Graph API error {resp.status_code}: {resp.text[:300]}")
        raise HTTPException(502, f"OneDrive API error: {resp.status_code}")
    return resp.json()


# ---------------------------------------------------------------------------
# OAuth2 Endpoints
# ---------------------------------------------------------------------------

@router.get("/status")
def onedrive_status(request: Request):
    """Check if OneDrive integration is configured."""
    configured = bool(MS_CLIENT_ID and MS_CLIENT_SECRET and MS_TENANT_ID)
    host = request.headers.get("x-forwarded-host", request.headers.get("host", "localhost"))
    scheme = request.headers.get("x-forwarded-proto", "https")
    redirect_uri = f"{scheme}://{host}{MS_REDIRECT_PATH}"
    return {
        "configured": configured,
        "scopes": SCOPES,
        "redirect_uri": redirect_uri,
    }


@router.post("/auth-url")
def get_auth_url(req: ConnectRequest, request: Request):
    """Generate Microsoft OAuth2 login URL for OneDrive."""
    from app.auth import check_project_permission
    user = getattr(getattr(request, 'state', None), 'user', None)
    if not user:
        raise HTTPException(401, "Not authenticated")
    perm = check_project_permission(user, req.project_slug, required_role="editor")
    if not perm:
        raise HTTPException(403, "Editor access required")

    app = _get_msal_app()

    host = request.headers.get("x-forwarded-host", request.headers.get("host", "localhost"))
    scheme = request.headers.get("x-forwarded-proto", "https")
    redirect_uri = f"{scheme}://{host}{MS_REDIRECT_PATH}"

    state = json.dumps({"project_slug": req.project_slug})

    # Pre-create a pending row so callback can correlate by project + user even
    # if multiple OAuth flows are in flight. user_id resolved at callback time.
    with _engine.connect() as conn:
        row = conn.execute(text(
            "SELECT id FROM public.dash_projects WHERE slug = :s"
        ), {"s": req.project_slug}).fetchone()
        owner_user_id = user.get("user_id", 0)

        conn.execute(text(
            "INSERT INTO public.dash_data_sources (project_slug, user_id, source_type, "
            "provider_class, status) "
            "VALUES (:slug, :uid, 'onedrive', 'onedrive', 'pending')"
        ), {"slug": req.project_slug, "uid": owner_user_id})
        conn.commit()

    auth_url = app.get_authorization_request_url(
        scopes=[s for s in SCOPES if s != "offline_access"],
        redirect_uri=redirect_uri,
        state=state,
    )

    return {"auth_url": auth_url, "state": state}


@router.get("/callback")
def oauth_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    """OAuth2 callback — exchange code for tokens, write back to pending row."""
    if error:
        logger.error(f"OneDrive OAuth error: {error}")
        return RedirectResponse(url="/ui/projects?error=onedrive_auth_failed")

    if not code or not state:
        raise HTTPException(400, "Missing code or state")

    try:
        state_data = json.loads(state)
    except Exception:
        raise HTTPException(400, "Invalid state parameter")

    project_slug = state_data.get("project_slug", "")
    frontend_redirect = state_data.get("frontend_redirect", f"/ui/project/{project_slug}/settings")

    app = _get_msal_app()
    host = request.headers.get("x-forwarded-host", request.headers.get("host", "localhost"))
    scheme = request.headers.get("x-forwarded-proto", "https")
    redirect_uri = f"{scheme}://{host}{MS_REDIRECT_PATH}"

    result = app.acquire_token_by_authorization_code(
        code,
        scopes=[s for s in SCOPES if s != "offline_access"],
        redirect_uri=redirect_uri,
    )

    if "access_token" not in result:
        logger.error(f"Token exchange failed: {result.get('error_description', 'unknown')}")
        return RedirectResponse(url=f"{frontend_redirect}?error=token_exchange_failed")

    access_token = result["access_token"]
    refresh_token = result.get("refresh_token", "")
    expires_in = result.get("expires_in", 3600)

    with _engine.connect() as conn:
        # Update the most-recent pending OneDrive row for this project. If none
        # exists (e.g. browser back-button), insert a fresh one.
        upd = conn.execute(text(
            "UPDATE public.dash_data_sources SET access_token = :at, refresh_token = :rt, "
            "token_expires_at = :exp, updated_at = NOW() "
            "WHERE id = ("
            "  SELECT id FROM public.dash_data_sources "
            "  WHERE project_slug = :slug AND source_type = 'onedrive' AND status = 'pending' "
            "  ORDER BY created_at DESC LIMIT 1"
            ") RETURNING id"
        ), {
            "slug": project_slug,
            "at": access_token, "rt": refresh_token,
            "exp": int(time.time()) + expires_in,
        })
        updated = upd.fetchone()

        if not updated:
            row = conn.execute(text(
                "SELECT user_id FROM public.dash_projects WHERE slug = :s"
            ), {"s": project_slug}).fetchone()
            user_id = row[0] if row else 0
            conn.execute(text(
                "INSERT INTO public.dash_data_sources (project_slug, user_id, source_type, "
                "provider_class, access_token, refresh_token, token_expires_at, status) "
                "VALUES (:slug, :uid, 'onedrive', 'onedrive', :at, :rt, :exp, 'pending')"
            ), {
                "slug": project_slug, "uid": user_id,
                "at": access_token, "rt": refresh_token,
                "exp": int(time.time()) + expires_in,
            })
        conn.commit()

    sep = "&" if "?" in frontend_redirect else "?"
    return RedirectResponse(url=f"{frontend_redirect}{sep}onedrive=connected")


# ---------------------------------------------------------------------------
# Browse OneDrive (no sites/drives layer — go straight to /me/drive)
# ---------------------------------------------------------------------------

@router.get("/my-drive")
def get_my_drive(request: Request, project: str = ""):
    """Return /me/drive metadata so the UI can show drive name + quota."""
    user = _get_user(request)
    sid = _get_pending_source(project, user["user_id"])
    token = _refresh_token_if_needed(sid)
    return _graph_get(token, "/me/drive")


@router.get("/browse")
def browse_folder(request: Request, project: str = "", folder_id: str = "root"):
    """Browse folders and files in the user's OneDrive."""
    user = _get_user(request)
    sid = _get_pending_source(project, user["user_id"])
    token = _refresh_token_if_needed(sid)

    endpoint = (
        "/me/drive/root/children" if folder_id == "root"
        else f"/me/drive/items/{folder_id}/children"
    )
    endpoint += "?$top=200&$select=id,name,size,file,folder,lastModifiedDateTime,webUrl"
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

    type_counts: dict[str, int] = {}
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
    """Finalize OneDrive connection — save folder config to the pending source.

    For OneDrive `drive_id` is always the literal string "me" (the helpers below
    interpret that and use the `/me/drive/...` Graph endpoints).
    """
    user = _get_user(request)
    source_id = _get_pending_source(req.project_slug, user["user_id"])

    with _engine.connect() as conn:
        conn.execute(text(
            "UPDATE public.dash_data_sources SET "
            "drive_id = :drive_id, folder_path = :folder_path, folder_id = :folder_id, "
            "site_name = :site_name, "
            "file_types = :file_types, status = 'active', updated_at = NOW() "
            "WHERE id = :id"
        ), {
            "drive_id": "me",
            "folder_path": req.folder_path,
            "folder_id": req.folder_id,
            "site_name": "OneDrive",
            "file_types": json.dumps(req.file_types),
            "id": source_id,
        })
        conn.commit()

    return {"status": "connected", "source_id": source_id}


# ---------------------------------------------------------------------------
# List Sources
# ---------------------------------------------------------------------------

@router.get("/sources")
def list_sources(request: Request, project: str = ""):
    """List all OneDrive sources for a project."""
    user = _get_user(request)

    with _engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, site_name, folder_path, file_types, last_sync_at, status, "
            "sync_state, created_at FROM public.dash_data_sources "
            "WHERE project_slug = :slug AND user_id = :uid AND source_type = 'onedrive' "
            "AND status != 'deleted' ORDER BY created_at DESC"
        ), {"slug": project, "uid": user["user_id"]}).fetchall()

    sources = []
    for r in rows:
        sync_state = r[6] if isinstance(r[6], dict) else json.loads(r[6]) if r[6] else {}
        sources.append({
            "id": r[0], "site_name": r[1] or "OneDrive", "folder_path": r[2],
            "file_types": r[3] if isinstance(r[3], list) else json.loads(r[3]) if r[3] else [],
            "last_sync_at": str(r[4]) if r[4] else None,
            "status": r[5],
            "files_synced": len(sync_state.get("files", {})),
            "last_error": sync_state.get("last_error", ""),
            "created_at": str(r[7]) if r[7] else None,
        })

    return {"sources": sources}


@router.delete("/sources/{source_id}")
def delete_source(source_id: int, request: Request):
    """Disconnect a OneDrive source."""
    user = _get_user(request)

    with _engine.connect() as conn:
        conn.execute(text(
            "UPDATE public.dash_data_sources SET status = 'deleted', updated_at = NOW() "
            "WHERE id = :id AND user_id = :uid AND source_type = 'onedrive'"
        ), {"id": source_id, "uid": user["user_id"]})
        conn.commit()

    return {"status": "deleted"}


_VALID_SCHEDULES = {"manual", "hourly", "daily", "weekly"}


class ScheduleUpdateRequest(BaseModel):
    sync_schedule: str


@router.post("/sources/{source_id}/schedule")
def update_source_schedule(source_id: int, req: ScheduleUpdateRequest, request: Request):
    """Update the sync_schedule for a OneDrive source."""
    from app.auth import check_project_permission
    user = _get_user(request)
    sched = (req.sync_schedule or "").strip().lower()
    if sched not in _VALID_SCHEDULES:
        raise HTTPException(400, f"Invalid sync_schedule (must be one of {sorted(_VALID_SCHEDULES)})")

    with _engine.connect() as conn:
        row = conn.execute(text(
            "SELECT project_slug FROM public.dash_data_sources WHERE id = :id AND source_type = 'onedrive'"
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
    """Sync files from OneDrive to Dash. Returns SSE stream of progress."""
    from starlette.responses import StreamingResponse
    import queue

    user = _get_user(request)

    with _engine.connect() as conn:
        row = conn.execute(text(
            "SELECT id, project_slug, drive_id, folder_id, file_types, sync_state "
            "FROM public.dash_data_sources WHERE id = :id AND user_id = :uid "
            "AND source_type = 'onedrive' AND status = 'active'"
        ), {"id": req.source_id, "uid": user["user_id"]}).fetchone()

    if not row:
        raise HTTPException(404, "Source not found or not active")

    source_id, project_slug, drive_id, folder_id, file_types_raw, sync_state_raw = row
    drive_id = drive_id or "me"
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

            all_files = _list_files_recursive(token, drive_id, folder_id, file_types)
            _emit("Scanning", f"Found {len(all_files)} supported files")

            to_download = []
            for f in all_files:
                key = f["id"]
                prev = synced_files.get(key, {})
                if prev.get("modified") != f["modified"] or prev.get("size") != f["size"]:
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
                    file_bytes = _download_file(token, drive_id, f["id"])
                    _emit(f"Processing ({i+1}/{len(to_download)})", f"{fname} ({len(file_bytes):,} bytes)")

                    ext = Path(fname).suffix.lower()
                    r = _process_onedrive_file(file_bytes, ext, project_slug, fname, _emit)

                    tables_count = len(r.get("tables", []))
                    text_len = len(r.get("text", ""))
                    results["tables"] += tables_count
                    if text_len > 0:
                        results["docs"] += 1
                    results["success"] += 1

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
    """Get the most recent pending/active OneDrive source for a project."""
    with _engine.connect() as conn:
        row = conn.execute(text(
            "SELECT id FROM public.dash_data_sources "
            "WHERE project_slug = :slug AND user_id = :uid AND source_type = 'onedrive' "
            "AND status IN ('pending', 'active') ORDER BY created_at DESC LIMIT 1"
        ), {"slug": project_slug, "uid": user_id}).fetchone()

    if not row:
        raise HTTPException(404, "No OneDrive connection found. Please sign in first.")
    return row[0]


def _drive_root(drive_id: str) -> str:
    """Return the Graph endpoint root for a OneDrive drive_id.

    OneDrive uses /me/drive when drive_id == "me"; otherwise fall back to
    /drives/{drive_id} (e.g. business drive picked by ID).
    """
    if not drive_id or drive_id == "me":
        return "/me/drive"
    return f"/drives/{drive_id}"


def _list_files_recursive(token: str, drive_id: str, folder_id: str, file_types: list[str], depth: int = 0) -> list[dict]:
    """Recursively list all supported files in a OneDrive folder."""
    if depth > 5:
        return []

    root = _drive_root(drive_id)
    if folder_id == "root":
        endpoint = f"{root}/root/children"
    else:
        endpoint = f"{root}/items/{folder_id}/children"
    endpoint += "?$top=200&$select=id,name,size,file,folder,lastModifiedDateTime"

    data = _graph_get(token, endpoint)

    files = []
    for item in data.get("value", []):
        if "folder" in item:
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
    """Download a file from OneDrive via Graph API."""
    import httpx
    root = _drive_root(drive_id)
    resp = httpx.get(
        f"{GRAPH_BASE}{root}/items/{item_id}/content",
        headers={"Authorization": f"Bearer {token}"},
        timeout=120,
        follow_redirects=True,
    )
    if resp.status_code != 200:
        raise Exception(f"Download failed: HTTP {resp.status_code}")
    return resp.content


def _process_onedrive_file(file_bytes: bytes, ext: str, project_slug: str, filename: str, _emit=None) -> dict:
    """Process a downloaded OneDrive file through the Dash upload pipeline."""
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

    table_name = tbl.get("name", "onedrive_data")

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


def _mask(secret: str) -> str:
    if not secret:
        return ""
    if len(secret) <= 8:
        return "*" * len(secret)
    return secret[:4] + "*" * (len(secret) - 8) + secret[-4:]


@router.get("/admin/config")
def get_admin_config(request: Request):
    """Get OneDrive config status (super-admin only)."""
    user = _get_user(request)
    from app.auth import SUPER_ADMIN
    if user.get("username") != SUPER_ADMIN:
        raise HTTPException(403, "Admin only")

    host = request.headers.get("x-forwarded-host", request.headers.get("host", "localhost"))
    scheme = request.headers.get("x-forwarded-proto", "https")
    redirect_uri = f"{scheme}://{host}{MS_REDIRECT_PATH}"

    return {
        "configured": bool(MS_CLIENT_ID and MS_CLIENT_SECRET and MS_TENANT_ID),
        "ms_client_id": _mask(MS_CLIENT_ID),
        "ms_tenant_id": MS_TENANT_ID or "common",
        "has_secret": bool(MS_CLIENT_SECRET),
        "redirect_uri": redirect_uri,
    }


@router.post("/admin/config")
def save_admin_config(req: AdminConfigRequest, request: Request):
    """Save shared OneDrive (Microsoft Entra) config to .env (super-admin only)."""
    user = _get_user(request)
    from app.auth import SUPER_ADMIN
    if user.get("username") != SUPER_ADMIN:
        raise HTTPException(403, "Admin only")

    if not req.ms_client_id:
        raise HTTPException(400, "ms_client_id is required")

    env_lines = []
    if _ENV_FILE.exists():
        env_lines = _ENV_FILE.read_text().splitlines()

    updates = {
        "MS_CLIENT_ID": req.ms_client_id,
        "MS_TENANT_ID": req.ms_tenant_id or "common",
    }
    if req.ms_client_secret:
        updates["MS_CLIENT_SECRET"] = req.ms_client_secret

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

    global MS_CLIENT_ID, MS_CLIENT_SECRET, MS_TENANT_ID
    MS_CLIENT_ID = req.ms_client_id
    MS_TENANT_ID = req.ms_tenant_id or "common"
    if req.ms_client_secret:
        MS_CLIENT_SECRET = req.ms_client_secret

    return {"status": "saved", "configured": bool(MS_CLIENT_ID and MS_CLIENT_SECRET and MS_TENANT_ID)}


# ---------------------------------------------------------------------------
# Init — called on app startup
# ---------------------------------------------------------------------------

def init_onedrive():
    """Initialize OneDrive connector. Called from app/main.py at startup.

    The shared `dash_data_sources` table is bootstrapped by the SharePoint
    connector — we simply rely on it. We re-validate MSAL singleton config
    via env so config errors surface in logs at boot rather than first auth.
    """
    logger.info("OneDrive connector initialized (tenant=%s, configured=%s)",
                MS_TENANT_ID, bool(MS_CLIENT_ID and MS_CLIENT_SECRET))
