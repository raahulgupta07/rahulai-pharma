"""Stdio MCP server exposing Dash agents as MCP tools.

This is a *separate* process — invoked by Claude Desktop (or Cursor /
Cline) via stdio. It does NOT import any of Dash's heavy dependencies
(no `app/`, no DB, no agno). It just talks HTTP to a running
``dash-api`` instance.

Tools exposed:

- ``dash_list_projects()`` — list user's projects (slug + name)
- ``dash_chat(project_slug, message)`` — send a chat message to a
  project's agent team and return the assistant text
- ``dash_query_sql(project_slug, sql)`` — run a read-only SQL query
  against a connected source via ``/api/connectors/query``
- ``dash_get_brain(project_slug, query)`` — search Company Brain
  (glossary / formulas / aliases / patterns)

Auth: ``DASH_API_TOKEN`` env var, sent as ``Authorization: Bearer ...``.
Base URL: ``DASH_API_BASE`` (default ``http://localhost:8001``).

Self-contained: no imports from ``app/`` or ``dash/`` packages.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from contextvars import ContextVar
from typing import Any

import httpx

# ContextVar carrying the active project slug for in-process tool calls
# (Phase 5.2). Set by each tool entry-point; read by helpers that need
# project scope (e.g. apply_skill, run_metric).
_CTX_PROJECT: ContextVar[str] = ContextVar("_CTX_PROJECT", default="")

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as exc:  # pragma: no cover
    sys.stderr.write(
        "ERROR: `mcp` package not installed. Run: pip install mcp>=1.0\n"
    )
    raise SystemExit(1) from exc


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DEFAULT_BASE = "http://localhost:8001"
TIMEOUT_S = 60.0


def _base_url() -> str:
    return os.environ.get("DASH_API_BASE", DEFAULT_BASE).rstrip("/")


def _headers() -> dict[str, str]:
    token = os.environ.get("DASH_API_TOKEN", "").strip()
    h = {"Accept": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _err(msg: str) -> str:
    return f"ERROR: {msg}"


def _http_get(path: str, params: dict | None = None) -> Any:
    url = f"{_base_url()}{path}"
    try:
        r = httpx.get(url, headers=_headers(), params=params, timeout=TIMEOUT_S)
    except httpx.HTTPError as e:
        return _err(f"network error calling {path}: {e}")
    if r.status_code == 401:
        return _err("401 unauthorized — check DASH_API_TOKEN")
    if r.status_code >= 400:
        return _err(f"{r.status_code} from {path}: {r.text[:300]}")
    try:
        return r.json()
    except Exception:
        return r.text


def _http_post(
    path: str, data: dict | None = None, json_body: dict | None = None,
) -> Any:
    url = f"{_base_url()}{path}"
    try:
        r = httpx.post(
            url,
            headers=_headers(),
            data=data,
            json=json_body,
            timeout=TIMEOUT_S,
        )
    except httpx.HTTPError as e:
        return _err(f"network error calling {path}: {e}")
    if r.status_code == 401:
        return _err("401 unauthorized — check DASH_API_TOKEN")
    if r.status_code >= 400:
        return _err(f"{r.status_code} from {path}: {r.text[:300]}")
    try:
        return r.json()
    except Exception:
        return r.text


# ---------------------------------------------------------------------------
# MCP server + tools
# ---------------------------------------------------------------------------

mcp = FastMCP("dash")


@mcp.tool()
def dash_list_projects() -> str:
    """List Dash projects available to the authenticated user.

    Returns a JSON-encoded list of ``{slug, name, agent_name}`` objects.
    """
    res = _http_get("/api/projects")
    if isinstance(res, str):
        return res  # error string
    projects = res if isinstance(res, list) else res.get("projects", [])
    out = [
        {
            "slug": p.get("slug"),
            "name": p.get("name"),
            "agent_name": p.get("agent_name"),
        }
        for p in projects
        if isinstance(p, dict)
    ]
    return json.dumps(out, ensure_ascii=False)[:8000]


@mcp.tool()
def dash_chat(project_slug: str, message: str) -> str:
    """Send a chat message to a Dash project's agent team.

    The message is routed by the project's Leader to Analyst, Researcher,
    Engineer, and/or Data Scientist as appropriate. Returns the
    synthesized assistant text response.

    Args:
        project_slug: Project slug (e.g. ``proj_demo_crm``). Use
            ``dash_list_projects`` to discover slugs.
        message: User question / instruction in natural language.
    """
    if not project_slug or not message:
        return _err("project_slug and message are both required")
    res = _http_post(
        f"/api/projects/{project_slug}/chat",
        data={"message": message, "stream": "false"},
    )
    if isinstance(res, str):
        return res[:8000]
    # Endpoint may return a dict with assistant text; be liberal.
    if isinstance(res, dict):
        for key in ("response", "answer", "content", "text", "message"):
            v = res.get(key)
            if isinstance(v, str) and v.strip():
                return v[:8000]
        return json.dumps(res, ensure_ascii=False)[:8000]
    return str(res)[:8000]


@mcp.tool()
def dash_query_sql(project_slug: str, sql: str) -> str:
    """Run a read-only SQL query against a Dash project's connected source.

    Uses the existing ``/api/connectors/query`` endpoint, which enforces
    read-only access, a 30s timeout, and a 10K row cap.

    Args:
        project_slug: Project slug whose data source to query.
        sql: A single SELECT statement.
    """
    if not project_slug or not sql:
        return _err("project_slug and sql are both required")
    if not sql.strip().lower().startswith(("select", "with")):
        return _err("only SELECT / WITH queries are allowed")
    res = _http_post(
        "/api/connectors/query",
        json_body={"project_slug": project_slug, "sql": sql},
    )
    if isinstance(res, str):
        return res[:8000]
    return json.dumps(res, ensure_ascii=False, default=str)[:8000]


@mcp.tool()
def dash_get_brain(project_slug: str, query: str) -> str:
    """Search the Company Brain (glossary, formulas, aliases, patterns).

    Returns brain entries scoped to the project plus any global entries
    matching ``query``.

    Args:
        project_slug: Project slug for project-scoped brain entries.
        query: Free-text search string.
    """
    if not query:
        return _err("query required")
    res = _http_get(
        "/api/brain/entries",
        params={"q": query, "project_slug": project_slug or "", "scope": "all"},
    )
    if isinstance(res, str):
        return res[:8000]
    return json.dumps(res, ensure_ascii=False, default=str)[:8000]


# ---------------------------------------------------------------------------
# Phase 5.2 — In-process tools (require Dash deps on PYTHONPATH)
# ---------------------------------------------------------------------------


def _get_sql_engine_safe():
    """Return shared SQL engine, or None if Dash deps unavailable."""
    try:
        from db.session import get_sql_engine  # type: ignore
        return get_sql_engine()
    except Exception:
        return None


@mcp.tool()
def apply_skill(project_slug: str, skill_query: str) -> dict:
    """Apply a proven skill from the Voyager skill library to a project.

    The skill_query is matched against ``dash.dash_skill_library`` by name
    (ILIKE). Best-match skill is executed via ``dash.tools.apply_skill``.

    Args:
        project_slug: Project slug (sets ``_CTX_PROJECT`` ContextVar).
        skill_query: Free-text name/keyword to match a skill.
    """
    if not project_slug or not skill_query:
        return {"error": "project_slug and skill_query are both required"}
    tok = _CTX_PROJECT.set(project_slug)
    try:
        eng = _get_sql_engine_safe()
        if eng is None:
            return {"error": "Dash dependencies not available in this MCP runtime"}
        from sqlalchemy import text as _text  # type: ignore

        with eng.connect() as conn:
            row = conn.execute(
                _text(
                    "SELECT id, name FROM public.dash_skill_library "
                    "WHERE project_slug = :p AND status = 'active' "
                    "AND (name ILIKE :q OR description ILIKE :q) "
                    "ORDER BY success_count DESC NULLS LAST LIMIT 1"
                ),
                {"p": project_slug, "q": f"%{skill_query}%"},
            ).fetchone()
        if not row:
            return {"error": f"no active skill matched query: {skill_query}"}
        skill_id, skill_name = int(row[0]), row[1]
        try:
            from dash.tools.apply_skill import apply_skill as _apply_skill  # type: ignore
        except Exception as e:
            return {"error": f"apply_skill import failed: {e}"}
        result_json = _apply_skill(skill_id=skill_id, params={})
        try:
            result = json.loads(result_json) if isinstance(result_json, str) else result_json
        except Exception:
            result = {"raw": str(result_json)[:8000]}
        return {"ok": True, "skill_id": skill_id, "skill_name": skill_name, "result": result}
    except Exception as e:
        return {"error": str(e)[:500]}
    finally:
        _CTX_PROJECT.reset(tok)


@mcp.tool()
def run_metric(project_slug: str, metric_name: str, filters: dict | None = None) -> dict:
    """Execute a named metric from ``dash.dash_metric_definitions``.

    Loads the spec via ``load_definition``, then runs it through
    ``metric_compiler.run_metric`` (read-only, project-scoped).

    Args:
        project_slug: Project slug.
        metric_name: Metric name (case-insensitive match).
        filters: Optional extra ``{column: value}`` filters (op='=').
    """
    if not project_slug or not metric_name:
        return {"error": "project_slug and metric_name are both required"}
    tok = _CTX_PROJECT.set(project_slug)
    try:
        try:
            from dash.tools.metric_compiler import (  # type: ignore
                load_definition,
                run_metric as _run_metric,
            )
        except Exception as e:
            return {"error": f"metric_compiler import failed: {e}"}
        spec = load_definition(project_slug, metric_name)
        if not spec:
            return {"error": f"metric '{metric_name}' not found for project"}
        extra: list[dict] = []
        if isinstance(filters, dict):
            for col, val in filters.items():
                extra.append({"column": str(col), "op": "=", "value": val})
        result = _run_metric(
            project_slug=project_slug,
            spec=spec,
            extra_filters=extra or None,
        )
        return result if isinstance(result, dict) else {"result": result}
    except Exception as e:
        return {"error": str(e)[:500]}
    finally:
        _CTX_PROJECT.reset(tok)


@mcp.tool()
def deep_research(project_slug: str, question: str) -> dict:
    """Run the 9-stage Deep Research pipeline for a question.

    Calls ``dash.tools.deep_research.DeepResearch().run(question,
    project_slug)``. Strips ``pdf_bytes`` (MCP cannot carry binary inline);
    when the pipeline persists a run, includes a pointer to
    ``/api/research/{run_id}/pdf``.

    Args:
        project_slug: Project slug.
        question: Research question in natural language.
    """
    if not project_slug or not question:
        return {"error": "project_slug and question are both required"}
    tok = _CTX_PROJECT.set(project_slug)
    try:
        try:
            from dash.tools.deep_research import DeepResearch  # type: ignore
        except Exception as e:
            return {"error": f"deep_research import failed: {e}"}
        out = DeepResearch(project_slug=project_slug).run(question, project_slug)
        if not isinstance(out, dict):
            return {"result": str(out)[:8000]}
        pdf_bytes = out.pop("pdf_bytes", None)
        if pdf_bytes:
            out["pdf_size_bytes"] = len(pdf_bytes)
            run_id = out.get("run_id") or (out.get("spec", {}) or {}).get("run_id")
            out["pdf_url"] = f"/api/research/{run_id}/pdf" if run_id else None
        return out
    except Exception as e:
        return {"error": str(e)[:500]}
    finally:
        _CTX_PROJECT.reset(tok)


# ---------------------------------------------------------------------------
# Phase 5.2 — MCP Resources
# ---------------------------------------------------------------------------


@mcp.resource("dash://schema/{project_slug}")
def project_schema(project_slug: str) -> str:
    """Project table list + column info from ``dash_table_metadata``.

    URI template: ``dash://schema/{project_slug}``. Returns JSON.
    """
    try:
        eng = _get_sql_engine_safe()
        if eng is None:
            return json.dumps({"error": "Dash dependencies not available"})
        from sqlalchemy import text as _text  # type: ignore

        with eng.connect() as conn:
            rows = conn.execute(
                _text(
                    "SELECT table_name, metadata, row_count, updated_at "
                    "FROM public.dash_table_metadata "
                    "WHERE project_slug = :p "
                    "ORDER BY table_name"
                ),
                {"p": project_slug},
            ).fetchall()
        tables = []
        for r in rows:
            meta = r[1] if isinstance(r[1], dict) else {}
            tables.append({
                "name": r[0],
                "columns": meta.get("columns") or meta.get("schema") or [],
                "purpose": meta.get("purpose"),
                "grain": meta.get("grain"),
                "row_count": r[2],
                "updated_at": str(r[3]) if r[3] else None,
            })
        return json.dumps(
            {"project_slug": project_slug, "table_count": len(tables), "tables": tables},
            ensure_ascii=False,
            default=str,
        )
    except Exception as e:
        return json.dumps({"error": str(e)[:500]})


@mcp.resource("dash://skills/{project_slug}")
def skill_list(project_slug: str) -> str:
    """Top 50 active skills from ``dash_skill_library`` for a project.

    URI template: ``dash://skills/{project_slug}``. Ordered by
    success_count DESC.
    """
    try:
        eng = _get_sql_engine_safe()
        if eng is None:
            return json.dumps({"error": "Dash dependencies not available"})
        from sqlalchemy import text as _text  # type: ignore

        with eng.connect() as conn:
            rows = conn.execute(
                _text(
                    "SELECT id, name, description, sql_template, params_schema, "
                    "       success_count, avg_judge_score, status, updated_at "
                    "FROM public.dash_skill_library "
                    "WHERE project_slug = :p AND status = 'active' "
                    "ORDER BY success_count DESC NULLS LAST LIMIT 50"
                ),
                {"p": project_slug},
            ).fetchall()
        skills = [
            {
                "id": r[0],
                "name": r[1],
                "description": r[2],
                "sql_template": (r[3] or "")[:2000],
                "params_schema": r[4],
                "success_count": r[5],
                "avg_judge_score": float(r[6]) if r[6] is not None else None,
                "status": r[7],
                "updated_at": str(r[8]) if r[8] else None,
            }
            for r in rows
        ]
        return json.dumps(
            {"project_slug": project_slug, "count": len(skills), "skills": skills},
            ensure_ascii=False,
            default=str,
        )
    except Exception as e:
        return json.dumps({"error": str(e)[:500]})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Run the MCP server over stdio (the Claude Desktop default)."""
    parser = argparse.ArgumentParser(
        prog="python -m mcp_server",
        description="Dash MCP server (stdio) — exposes Dash agents to "
        "Claude Desktop / Cursor / Cline.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Print config and exit without starting the server.",
    )
    args = parser.parse_args()

    if args.check:
        sys.stdout.write(
            f"DASH_API_BASE = {_base_url()}\n"
            f"DASH_API_TOKEN = {'<set>' if os.environ.get('DASH_API_TOKEN') else '<unset>'}\n"
            "Tools: dash_list_projects, dash_chat, dash_query_sql, "
            "dash_get_brain, apply_skill, run_metric, deep_research\n"
            "Resources: dash://schema/{slug}, dash://skills/{slug}\n"
        )
        return

    # FastMCP.run() defaults to stdio transport.
    mcp.run()


if __name__ == "__main__":
    main()
