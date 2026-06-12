"""Chat-scope ContextVars.

The chat endpoint (`app/projects.py`) sets CUR_SESSION_ID + CUR_PROJECT_SLUG
around `team.run` so tools know which session/project they're running under.

NOTE: the Obsidian-style dash_links write helpers (extract_table_refs /
link_chat_cites_tables / link_chat_uses_skill) were removed 2026-06-06 — the
GraphPanel/LinkedBy UIs and their /api/graph + /api/links backends were dead in
single-agent mode, so those per-chat writes had no reader. Only the ContextVars
remain (cheap, still set by the chat hot path).
"""
from __future__ import annotations

from contextvars import ContextVar

CUR_SESSION_ID: ContextVar[str | None] = ContextVar("CUR_SESSION_ID", default=None)
CUR_PROJECT_SLUG: ContextVar[str | None] = ContextVar("CUR_PROJECT_SLUG", default=None)
# The raw user question for the current chat turn. Set by project_chat before the
# agent runs so the run_sql_query capture hook (continuous query learning, P1) can
# pair the agent's generated SQL with the question that prompted it. Best-effort.
CUR_QUESTION: ContextVar[str | None] = ContextVar("CUR_QUESTION", default=None)
