"""
Router Tools
=============

Tool factory functions for the Router Agent.
Each factory returns a closure decorated with @tool for use by the routing agent.
"""

from __future__ import annotations

import glob
import json
import logging
from pathlib import Path

from agno.tools import tool

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool 1: Inspect Catalog
# ---------------------------------------------------------------------------

def create_inspect_catalog_tool(projects: list[dict]):
    """Return a tool that lists all available projects with metadata."""

    # Pre-build the catalog string once at creation time
    lines: list[str] = []
    for p in projects:
        slug = p.get("slug", "?")
        name = p.get("name", slug)
        role = p.get("agent_role", "")
        tables = p.get("tables", [])
        columns = p.get("columns", [])
        keywords = p.get("persona_keywords", [])

        lines.append(f"### {name} (`{slug}`)")
        if role:
            lines.append(f"**Role:** {role}")
        if tables:
            preview = ", ".join(tables[:10])
            suffix = f" ... (+{len(tables) - 10} more)" if len(tables) > 10 else ""
            lines.append(f"**Tables:** {preview}{suffix}")
        if columns:
            preview = ", ".join(columns[:15])
            suffix = f" ... (+{len(columns) - 15} more)" if len(columns) > 15 else ""
            lines.append(f"**Key columns:** {preview}{suffix}")
        if keywords:
            preview = ", ".join(keywords[:10])
            suffix = f" ... (+{len(keywords) - 10} more)" if len(keywords) > 10 else ""
            lines.append(f"**Domain keywords:** {preview}{suffix}")
        lines.append("")  # blank separator

    catalog_text = "\n".join(lines) if lines else "No projects available."

    @tool(
        name="inspect_catalog",
        description="List all available projects with their tables, columns, and domain keywords. Call this first.",
    )
    def inspect_catalog() -> str:
        return catalog_text

    return inspect_catalog


# ---------------------------------------------------------------------------
# Tool 2: Inspect Project Detail
# ---------------------------------------------------------------------------

def create_inspect_detail_tool():
    """Return a tool that reads trained table metadata for a project."""

    @tool(
        name="inspect_project_detail",
        description=(
            "Get detailed trained metadata for a project: table descriptions, "
            "purpose, grain, usage patterns. Use when catalog isn't enough."
        ),
    )
    def inspect_project_detail(slug: str) -> str:
        pattern = str(Path("knowledge") / slug / "tables" / "*.json")
        files = glob.glob(pattern)
        if not files:
            return f"No detailed metadata available for project `{slug}`."

        sections: list[str] = []
        for fpath in sorted(files):
            try:
                with open(fpath, "r") as f:
                    meta = json.load(f)
            except Exception:
                continue

            table_name = meta.get("table_name", Path(fpath).stem)
            desc = meta.get("description", "")
            purpose = meta.get("table_purpose", "")
            grain = meta.get("grain", "")
            pks = meta.get("primary_keys", [])
            usage = meta.get("usage_patterns", [])
            cols = meta.get("columns", [])

            parts = [f"### {table_name}"]
            if desc:
                parts.append(f"**Description:** {desc}")
            if purpose:
                parts.append(f"**Purpose:** {purpose}")
            if grain:
                parts.append(f"**Grain:** {grain}")
            if pks:
                parts.append(f"**Primary keys:** {', '.join(pks)}")
            if usage:
                if isinstance(usage, list):
                    parts.append("**Usage patterns:** " + "; ".join(str(u) for u in usage))
                else:
                    parts.append(f"**Usage patterns:** {usage}")
            if cols:
                col_lines = [f"  - `{c.get('name', '?')}`: {c.get('description', '')}" for c in cols[:20]]
                parts.append("**Columns:**\n" + "\n".join(col_lines))
            sections.append("\n".join(parts))

        if not sections:
            return f"No detailed metadata available for project `{slug}`."

        return "\n\n".join(sections)

    return inspect_project_detail


# ---------------------------------------------------------------------------
# Tool 3: Search Brain
# ---------------------------------------------------------------------------

def create_search_brain_tool(project_slugs: list[str] | None = None):
    """Return a tool that searches the Company Brain for business terms."""

    @tool(
        name="search_brain",
        description=(
            "Search Company Brain for business terms, aliases, glossary, "
            "org structure. Use when you see unfamiliar domain terms."
        ),
    )
    def search_brain(terms: str) -> str:
        from db import get_sql_engine
        from sqlalchemy import text

        term_list = [t.strip() for t in terms.split(",") if t.strip()]
        if not term_list:
            return "No search terms provided."

        patterns = [f"%{t}%" for t in term_list]

        slugs = project_slugs or []

        query = text(
            "SELECT category, name, definition, project_slug "
            "FROM public.dash_company_brain "
            "WHERE (project_slug IS NULL OR project_slug = ANY(:slugs)) "
            "AND (name ILIKE ANY(:patterns) OR definition ILIKE ANY(:patterns)) "
            "LIMIT 15"
        )

        try:
            engine = get_sql_engine()
            with engine.connect() as conn:
                rows = conn.execute(query, {"patterns": patterns, "slugs": slugs}).fetchall()
        except Exception as e:
            logger.warning("search_brain failed: %s", e)
            return f"Brain search failed: {e}"

        if not rows:
            return f"No matches found for: {terms}"

        lines: list[str] = []
        for row in rows:
            cat = row[0] or "GENERAL"
            name = row[1] or ""
            defn = row[2] or ""
            scope = f" [{row[3]}]" if row[3] else ""
            lines.append(f"[{cat.upper()}] **{name}**: {defn}{scope}")

        return "\n".join(lines)

    return search_brain


# ---------------------------------------------------------------------------
# Tool 4: Session Context
# ---------------------------------------------------------------------------

def create_session_context_tool(session_id: str | None):
    """Return a tool that checks previous routing for this session."""

    @tool(
        name="check_session_context",
        description=(
            "Check what project the previous message in this session was "
            "routed to. Use for follow-up questions."
        ),
    )
    def check_session_context() -> str:
        if session_id is None:
            return "No session context available."

        from db import get_sql_engine
        from sqlalchemy import text

        query = text(
            "SELECT project_slug FROM public.dash_chat_sessions "
            "WHERE session_id = :sid "
            "ORDER BY updated_at DESC LIMIT 1"
        )

        try:
            engine = get_sql_engine()
            with engine.connect() as conn:
                row = conn.execute(query, {"sid": session_id}).fetchone()
        except Exception as e:
            logger.warning("check_session_context failed: %s", e)
            return f"Session lookup failed: {e}"

        if row and row[0]:
            return f"Previous routing: `{row[0]}`"
        return "No previous routing found for this session."

    return check_session_context
