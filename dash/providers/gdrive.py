"""Google Drive provider — file/document source for the Researcher agent.

Token-based, not engine-based. Provides search/fetch tools rather than
SQL. Plugs into the per-agent provider abstraction so the Researcher can
list and pull docs scoped to a single Google Drive folder.

Mirrors the OneDrive provider pattern. Uses
``app.gdrive._refresh_token_if_needed`` (line 158 of app/gdrive.py) as
the token-validity helper, matching the SharePoint/OneDrive convention.
"""
from __future__ import annotations

import logging
from typing import Any

from dash.providers.base import BaseProvider, _DIALECT_QUOTE
from dash.providers.registry import register_provider_class

logger = logging.getLogger(__name__)


# OneDrive already registers the "files" dialect, but be defensive in case
# this module is imported first. setdefault keeps it idempotent.
_DIALECT_QUOTE.setdefault("files", ('"', '"'))


class GoogleDriveProvider(BaseProvider):
    def __init__(
        self,
        project_slug: str,
        source_id: int,
        name: str,
        config: dict | None = None,
    ):
        super().__init__(
            id=f"gdrive_{source_id}",
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
            from app.gdrive import _refresh_token_if_needed
            self.access_token = _refresh_token_if_needed(self.source_id)
            self.degraded = False
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "Google Drive provider setup failed for source %s", self.source_id
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
            from dash.providers.gdrive_tools import make_tools  # may not exist yet
            return make_tools(self)
        except ImportError:
            logger.debug("gdrive_tools module not yet present; emitting [] tools")
            return []

    def dialect_overlay(self) -> str:
        if self.degraded:
            return f"## SOURCE {self.name} (Google Drive) — DEGRADED: {self.last_error}\n"
        return (
            f"## SOURCE {self.name} (Google Drive · {self.mode})\n"
            f"Document source. Use `search_{self.id}(query)` to find docs by name/content,\n"
            f"`fetch_{self.id}(item_id)` to retrieve a doc's text, "
            f"`list_folder_{self.id}(folder_id)` to browse.\n"
            f"Google Workspace files auto-convert on fetch: "
            f"Sheets→xlsx, Docs→docx, Slides→pptx.\n"
        )


register_provider_class("gdrive", GoogleDriveProvider)
