"""Researcher tools for OneDrive provider — search, fetch, list_folder.

Mirrors :mod:`dash.providers.tool_factory` but for a non-SQL document
source. Each callable is decorated with Agno's ``@tool`` and suffixed
with the provider id so multiple OneDrive sources can coexist on the
same Researcher agent.

All tools return strings (the Researcher consumes plain text), cap
their output, and never raise past this layer — every error is
returned as a leading ``ERROR:`` / ``<X> ERROR:`` string so the agent
can reason about failure without crashing the team loop.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from agno.tools import tool

logger = logging.getLogger(__name__)

# 12KB cap on extracted text returned to the agent (Researcher context budget).
MAX_FETCH_BYTES = 12 * 1024


def _unavailable(provider) -> str:
    return (
        f"ERROR: source '{provider.name}' unavailable. "
        f"{provider.last_error or 'no access token'}"
    )


def make_tools(provider) -> list[Callable[..., Any]]:
    """Build the three Agno-compatible callables for ``provider``.

    Closures capture ``provider`` (not its token), so a refreshed
    ``provider.access_token`` after rotation is observed by every
    subsequent tool call.
    """
    pid = provider.id
    src_name = provider.name

    @tool(
        name=f"search_{pid}",
        description=(
            f"Search files in OneDrive source '{src_name}' by name/content. "
            "Returns up to 25 matches with id, name, type, modified date as TSV."
        ),
    )
    def search_files(query: str) -> str:
        if provider.degraded or not provider.access_token:
            return _unavailable(provider)
        try:
            from app.onedrive import _graph_get
            # Graph search: /me/drive/root/search(q='...'). Quotes inside
            # query must be doubled per OData rules.
            safe_q = (query or "").replace("'", "''")
            data = _graph_get(
                provider.access_token,
                f"/me/drive/root/search(q='{safe_q}')",
            )
            items = data.get("value", [])[:25]
            if not items:
                return "no matches"
            lines = ["id\tname\ttype\tmodified"]
            for it in items:
                kind = "folder" if "folder" in it else "file"
                lines.append(
                    f"{it.get('id', '')}\t{it.get('name', '')}\t{kind}\t"
                    f"{it.get('lastModifiedDateTime', '')}"
                )
            return "\n".join(lines)
        except Exception as e:
            logger.warning("search_%s failed: %s", pid, e)
            return f"SEARCH ERROR: {str(e)[:300]}"

    @tool(
        name=f"fetch_{pid}",
        description=(
            f"Fetch text content of a file from OneDrive '{src_name}' by item_id. "
            f"Returns extracted text (first {MAX_FETCH_BYTES // 1024}KB)."
        ),
    )
    def fetch_file(item_id: str) -> str:
        if provider.degraded or not provider.access_token:
            return _unavailable(provider)
        try:
            from app.onedrive import (
                _download_file,
                _graph_get,
                _process_onedrive_file,
            )
            # Resolve filename + extension via Graph metadata first.
            meta = _graph_get(
                provider.access_token, f"/me/drive/items/{item_id}"
            )
            filename = meta.get("name", "unknown")
            ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
            content = _download_file(provider.access_token, "me", item_id)
            result = _process_onedrive_file(
                content, ext, provider.project_slug, filename
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
            f"List children of a OneDrive folder in '{src_name}' by id "
            "('root' for drive root). Returns folders + files as TSV."
        ),
    )
    def list_folder(folder_id: str = "root") -> str:
        if provider.degraded or not provider.access_token:
            return _unavailable(provider)
        try:
            from app.onedrive import _graph_get
            endpoint = (
                "/me/drive/root/children"
                if folder_id == "root"
                else f"/me/drive/items/{folder_id}/children"
            )
            data = _graph_get(provider.access_token, endpoint)
            items = data.get("value", [])
            if not items:
                return "empty folder"
            lines = ["id\tname\ttype\tsize"]
            for it in items:
                kind = "folder" if "folder" in it else "file"
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
