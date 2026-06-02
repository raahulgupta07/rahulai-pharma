"""Researcher tools for Google Drive provider — search, fetch, list_folder.

Mirrors :mod:`dash.providers.onedrive_tools` but uses the Drive API v3
SDK (``googleapiclient``) instead of Graph REST. Google Workspace
files (Sheets/Docs/Slides) are exported to Office formats via the
existing ``app.gdrive._download_file`` helper, which inspects the
``workspace`` flag on the file_info dict.

All tools return strings, cap output, and never raise past this layer.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

from agno.tools import tool

logger = logging.getLogger(__name__)

# 12KB cap on extracted text returned to the agent.
MAX_FETCH_BYTES = 12 * 1024


def _unavailable(provider) -> str:
    return (
        f"ERROR: source '{provider.name}' unavailable. "
        f"{provider.last_error or 'no access token'}"
    )


def _resolve_token(provider) -> str | None:
    """Return a usable access token, refreshing via app.gdrive if missing."""
    token = provider.access_token
    if token:
        return token
    try:
        from app.gdrive import _refresh_token_if_needed
        token = _refresh_token_if_needed(provider.source_id)
        provider.access_token = token
        return token
    except Exception as exc:  # noqa: BLE001
        logger.warning("gdrive token refresh failed for %s: %s", provider.id, exc)
        return None


def make_tools(provider) -> list[Callable[..., Any]]:
    """Build the three Agno-compatible callables for ``provider``."""
    pid = provider.id
    src_name = provider.name

    @tool(
        name=f"search_{pid}",
        description=(
            f"Search files in Google Drive source '{src_name}' by name or "
            "full-text content. Returns up to 25 matches with id, name, "
            "mimeType, modified date as TSV."
        ),
    )
    def search_files(query: str) -> str:
        if provider.degraded:
            return _unavailable(provider)
        token = _resolve_token(provider)
        if not token:
            return _unavailable(provider)
        try:
            from app.gdrive import _get_drive_service
            service = _get_drive_service(token)
            # Single-quote escaping per Drive query language.
            safe_q = (query or "").replace("'", "\\'")
            q = (
                f"(name contains '{safe_q}' or fullText contains '{safe_q}') "
                "and trashed = false"
            )
            data = service.files().list(
                q=q,
                pageSize=25,
                fields="files(id,name,mimeType,modifiedTime)",
            ).execute()
            items = data.get("files", [])
            if not items:
                return "no matches"
            lines = ["id\tname\tmimeType\tmodified"]
            for it in items:
                lines.append(
                    f"{it.get('id', '')}\t{it.get('name', '')}\t"
                    f"{it.get('mimeType', '')}\t{it.get('modifiedTime', '')}"
                )
            return "\n".join(lines)
        except Exception as e:
            logger.warning("search_%s failed: %s", pid, e)
            return f"SEARCH ERROR: {str(e)[:300]}"

    @tool(
        name=f"fetch_{pid}",
        description=(
            f"Fetch text content of a file from Google Drive '{src_name}' by "
            f"file id. Workspace files (Docs/Sheets/Slides) are auto-exported. "
            f"Returns extracted text (first {MAX_FETCH_BYTES // 1024}KB)."
        ),
    )
    def fetch_file(item_id: str) -> str:
        if provider.degraded:
            return _unavailable(provider)
        token = _resolve_token(provider)
        if not token:
            return _unavailable(provider)
        try:
            from app.gdrive import (
                WORKSPACE_EXPORTS,
                _download_file,
                _get_drive_service,
                _process_gdrive_file,
            )
            service = _get_drive_service(token)
            meta = service.files().get(
                fileId=item_id,
                fields="id,name,mimeType,size",
            ).execute()
            mime = meta.get("mimeType", "")
            name = meta.get("name", "unknown")
            # Build the file_info dict that _download_file expects.
            if mime in WORKSPACE_EXPORTS:
                ext = WORKSPACE_EXPORTS[mime][0]
                file_info = {
                    "id": item_id, "name": name, "mime": mime,
                    "ext": ext, "workspace": True,
                }
            else:
                ext = Path(name).suffix.lower().lstrip(".")
                file_info = {
                    "id": item_id, "name": name, "mime": mime,
                    "ext": ext, "workspace": False,
                }
            content, actual_name = _download_file(service, file_info)
            result = _process_gdrive_file(
                content, ext, provider.project_slug, actual_name
            )
            txt = result.get("text") or result.get("text_content") or ""
            if not txt:
                return "no extractable text"
            return txt[:MAX_FETCH_BYTES]
        except Exception as e:
            logger.warning("fetch_%s failed: %s", pid, e)
            return f"FETCH ERROR: {str(e)[:300]}"

    @tool(
        name=f"list_folder_{pid}",
        description=(
            f"List children of a Google Drive folder in '{src_name}' by id "
            "('root' for My Drive root). Returns folders + files as TSV."
        ),
    )
    def list_folder(folder_id: str = "root") -> str:
        if provider.degraded:
            return _unavailable(provider)
        token = _resolve_token(provider)
        if not token:
            return _unavailable(provider)
        try:
            from app.gdrive import _get_drive_service
            service = _get_drive_service(token)
            data = service.files().list(
                q=f"'{folder_id}' in parents and trashed = false",
                pageSize=200,
                fields="files(id,name,mimeType,size,modifiedTime)",
            ).execute()
            items = data.get("files", [])
            if not items:
                return "empty folder"
            lines = ["id\tname\ttype\tsize"]
            for it in items:
                mime = it.get("mimeType", "")
                kind = (
                    "folder"
                    if mime == "application/vnd.google-apps.folder"
                    else "file"
                )
                lines.append(
                    f"{it.get('id', '')}\t{it.get('name', '')}\t{kind}\t"
                    f"{it.get('size', 0)}"
                )
            return "\n".join(lines)
        except Exception as e:
            logger.warning("list_folder_%s failed: %s", pid, e)
            return f"LIST ERROR: {str(e)[:300]}"

    return [search_files, fetch_file, list_folder]


__all__ = ["make_tools"]
