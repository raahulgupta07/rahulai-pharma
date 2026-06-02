"""SharePoint provider — file/document source for the Researcher agent.

Wraps the existing :mod:`app.sharepoint` connector (Microsoft Entra ID
OAuth2 + Graph API) into the per-agent :class:`BaseProvider` abstraction.
Token-based, not engine-based: there are no SQL engines to manage; the
provider holds an access token refreshed lazily via
``app.sharepoint._refresh_token_if_needed``.

Mirrors the OneDrive / Google Drive provider patterns exactly. Plugs into
the project-scoped registry so the Researcher can list and pull docs
scoped to a single SharePoint site/drive without leaking SQL tools.
"""
from __future__ import annotations

import logging
from typing import Any

from dash.providers.base import BaseProvider, _DIALECT_QUOTE
from dash.providers.registry import register_provider_class

logger = logging.getLogger(__name__)


# OneDrive / Google Drive already register the "files" dialect, but be
# defensive in case this module is imported first. setdefault keeps it
# idempotent across import order.
_DIALECT_QUOTE.setdefault("files", ('"', '"'))


class SharePointProvider(BaseProvider):
    def __init__(
        self,
        project_slug: str,
        source_id: int,
        name: str,
        config: dict | None = None,
    ):
        super().__init__(
            id=f"sharepoint_{source_id}",
            name=name,
            project_slug=project_slug,
            dialect="files",  # special non-SQL dialect
            agent_scope=(config or {}).get("agent_scope", "researcher_only"),
        )
        self.source_id = source_id
        self.config = config or {}
        self.mode = self.config.get("mode", "sync")
        self.access_token: str | None = None

    async def setup(self) -> None:
        try:
            from app.sharepoint import _refresh_token_if_needed
            self.access_token = _refresh_token_if_needed(self.source_id)
            self.degraded = False
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "SharePoint provider setup failed for source %s", self.source_id
            )
            self.degraded = True
            self.last_error = str(exc)[:300]

    async def teardown(self) -> None:
        self.access_token = None

    def introspect(self) -> dict[str, Any]:
        # Files don't have a tabular schema; return empty struct.
        return {"dialect": "files", "tables": [], "columns": {}, "fks": []}

    def health_check(self) -> bool:
        return self.access_token is not None and not self.degraded

    def emit_tools(self) -> list:
        # Researcher-style tools, not Analyst SQL tools.
        try:
            from dash.providers.sharepoint_tools import make_tools  # may not exist yet
            return make_tools(self)
        except ImportError:
            logger.debug("sharepoint_tools module not yet present; emitting [] tools")
            return []

    def dialect_overlay(self) -> str:
        if self.degraded:
            return f"## SOURCE {self.name} (SharePoint) — DEGRADED: {self.last_error}\n"
        site_name = self.config.get("site_name") or self.name
        drive_id = self.config.get("drive_id") or "<unknown>"
        return (
            f"## SOURCE {self.name} (SharePoint · {self.mode})\n"
            f"Document source. Site: {site_name}, Drive: {drive_id}.\n"
            f"Use `search_{self.id}(query)` to find docs by name/content,\n"
            f"`fetch_{self.id}(item_id)` to retrieve a doc's text, "
            f"`list_folder_{self.id}(folder_id)` to browse.\n"
            f"OAuth scopes: `Sites.Read.All` and `Files.Read.All` "
            f"(read-only Microsoft Graph access).\n"
        )


register_provider_class("sharepoint", SharePointProvider)
