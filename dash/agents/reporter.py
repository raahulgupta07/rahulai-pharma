"""Dash-OS Phase 2A — Reporter agent.

Mid-conversation file generation. Emits [FILE:{id}|{name}|{type}] tag for
frontend pickup.
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

_INSTRUCTIONS = """\
You are Reporter. When user asks for a downloadable file (PDF, PPTX, CSV, Excel, Word, JSON, Markdown):
1. Pick the right make_* tool by file type.
2. Build structured input — for PDF/DOCX use sections=[{heading,body}], for PPTX use slides=[{title,content}], for CSV use rows=[{col:val}], for XLSX use sheets={sheet_name: [rows]}.
3. Call the tool, get download_url back.
4. ALWAYS emit this tag after success so the frontend renders a download button:
   [FILE:{file_id}|{filename}|{file_type}]
5. NEVER inline-paste file contents — always link via download_url.

Themes for PPTX: midnight_executive, forest_moss, coral_energy, ocean_gradient, charcoal_minimal, teal_trust, berry_cream, cherry_bold.
"""


def build_reporter_agent(project_slug: Optional[str] = None, user_id: Optional[int] = None):
    try:
        from agno.agent import Agent
        from agno.models.openrouter import OpenRouter
    except Exception as e:
        logger.warning("agno not available: %s", e)
        return None

    try:
        from dash.tools.file_generation import (
            make_pdf, make_pptx, make_csv, make_xlsx, make_docx, make_json, make_md,
            list_generated_files,
        )
    except Exception as e:
        logger.warning("file_generation tools not loadable: %s", e)
        return None

    try:
        from dash.settings import CHAT_MODEL, OR_DATA_POLICY
    except Exception:
        CHAT_MODEL = "google/gemini-3-flash-preview"
        OR_DATA_POLICY = {"provider": {"data_collection": "allow"}}

    try:
        return Agent(
            name="Reporter",
            model=OpenRouter(id=CHAT_MODEL, extra_body=OR_DATA_POLICY),
            tools=[make_pdf, make_pptx, make_csv, make_xlsx, make_docx, make_json, make_md, list_generated_files],
            instructions=_INSTRUCTIONS,
        )
    except Exception as e:
        logger.warning("Reporter agent build failed: %s", e)
        return None
