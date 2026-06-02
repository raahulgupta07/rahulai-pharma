"""MCP tool registry.

Single source of truth for the eight Dash capabilities exposed via MCP.
Each entry is a dict::

    {
        "name": "dash_sql_query",
        "description": "...",
        "inputSchema": {... JSON Schema for params ...},
        "handler": callable(user_dict, **params) -> dict,
        "requires": "viewer" | "editor" | "admin" | None,   # project role
    }

The handler signature is::

    handler(user: dict, **params) -> dict

Handlers MUST be defensive: validate inputs, schema-qualify SQL with
``dash.*`` where appropriate, never raise raw stack traces back to the
client (return ``{"error": "..."}``).

Direct in-process imports are used (no HTTP shell-out) — this means the
MCP server pod needs the full Dash codebase on PYTHONPATH. The stdio
bridge container in ``compose.yaml`` (service ``dash-mcp``) shares the
same image as ``dash-api`` exactly for this reason.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from .auth import can_access_project

log = logging.getLogger("mcp.tools")

ToolHandler = Callable[..., dict[str, Any]]


# ---------------------------------------------------------------------------
# Lazy imports — each helper imports Dash modules on first call so that
# missing optional deps don't blow up the entire registry import.
# ---------------------------------------------------------------------------

def _safe_error(exc: Exception) -> dict[str, Any]:
    log.exception("mcp.tool.error %s", exc)
    return {"ok": False, "error": str(exc)[:500]}


def _get_ro_engine(slug: str):
    from db.session import get_project_readonly_engine  # type: ignore

    return get_project_readonly_engine(slug)


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def _tool_sql_query(user: dict, project_slug: str, sql: str, limit: int = 500) -> dict:
    """Read-only SELECT/WITH against a project's schema via the
    ``get_project_readonly_engine`` (RLS + read_only session vars
    enforced)."""
    if not project_slug or not sql:
        return {"ok": False, "error": "project_slug and sql required"}

    if not can_access_project(user, project_slug, "viewer"):
        return {"ok": False, "error": "forbidden"}

    head = sql.strip().lstrip("(").split(None, 1)[0].upper() if sql.strip() else ""
    if head not in {"SELECT", "WITH"}:
        return {"ok": False, "error": "only SELECT / WITH allowed"}

    limit = max(1, min(int(limit or 500), 5000))

    try:
        from sqlalchemy import text as _text

        eng = _get_ro_engine(project_slug)
        wrapped = f"SELECT * FROM ({sql.rstrip(';')}) AS _mcp_q LIMIT {limit}"
        with eng.connect() as conn:
            res = conn.execute(_text(wrapped))
            cols = list(res.keys())
            rows = [list(r) for r in res.fetchall()]
        return {"ok": True, "columns": cols, "rows": rows, "row_count": len(rows)}
    except Exception as e:
        return _safe_error(e)


def _tool_recall(user: dict, project_slug: str, q: str, top_k: int = 8) -> dict:
    """Unified recall across the project's KB / Brain / KG / Grounded
    Facts via ``app.recall_api`` (shipped today)."""
    if not project_slug or not q:
        return {"ok": False, "error": "project_slug and q required"}
    if not can_access_project(user, project_slug, "viewer"):
        return {"ok": False, "error": "forbidden"}

    try:
        from app.recall_api import recall_unified  # type: ignore
    except Exception:
        # Fallback: semantic_search if the dedicated module isn't around
        try:
            from dash.tools.semantic_search import search_all  # type: ignore

            hits = search_all(query=q, project_slug=project_slug, top_k=int(top_k))
            return {"ok": True, "results": hits[: int(top_k)]}
        except Exception as e:
            return _safe_error(e)

    try:
        hits = recall_unified(project_slug=project_slug, query=q, top_k=int(top_k))
        return {"ok": True, "results": hits}
    except Exception as e:
        return _safe_error(e)


def _tool_apply_skill(user: dict, project_slug: str, skill_id: str,
                       params: dict | None = None) -> dict:
    """Execute a proven skill from ``dash.dash_skill_library``. Honors
    role bindings — viewer cannot apply skills marked editor-only."""
    if not project_slug or not skill_id:
        return {"ok": False, "error": "project_slug and skill_id required"}
    if not can_access_project(user, project_slug, "editor"):
        return {"ok": False, "error": "forbidden (editor role required)"}

    try:
        from dash.tools.apply_skill import apply_skill  # type: ignore

        return {"ok": True, "result": apply_skill(
            skill_id=skill_id,
            params=params or {},
        )}
    except Exception as e:
        return _safe_error(e)


def _tool_search_brain(user: dict, query: str, scope: str = "global",
                        project_slug: str | None = None) -> dict:
    """Search Company Brain (glossary / formulas / aliases / patterns /
    org / thresholds). Scope ∈ {global, project, personal}."""
    if not query:
        return {"ok": False, "error": "query required"}
    if scope == "project" and not project_slug:
        return {"ok": False, "error": "project scope requires project_slug"}
    if scope == "project" and not can_access_project(user, project_slug, "viewer"):
        return {"ok": False, "error": "forbidden"}

    try:
        from app.brain import search_brain_entries  # type: ignore

        hits = search_brain_entries(
            query=query,
            scope=scope,
            project_slug=project_slug,
            user_id=user.get("user_id") if scope == "personal" else None,
            limit=20,
        )
        return {"ok": True, "results": hits}
    except Exception:
        # Fallback: raw SQL against dash_company_brain
        try:
            from sqlalchemy import create_engine, text
            from sqlalchemy.pool import NullPool

            from db import db_url  # type: ignore

            eng = create_engine(db_url, poolclass=NullPool)
            with eng.connect() as conn:
                sql = (
                    "SELECT name, category, definition, project_slug "
                    "FROM dash.dash_company_brain "
                    "WHERE (name ILIKE :q OR definition ILIKE :q) "
                )
                params: dict = {"q": f"%{query}%"}
                if scope == "global":
                    sql += "AND project_slug IS NULL AND user_id IS NULL "
                elif scope == "project":
                    sql += "AND project_slug = :p "
                    params["p"] = project_slug
                elif scope == "personal":
                    sql += "AND user_id = :u "
                    params["u"] = user.get("user_id")
                sql += "LIMIT 20"
                rows = conn.execute(text(sql), params).fetchall()
            eng.dispose()
            return {
                "ok": True,
                "results": [
                    {"name": r[0], "category": r[1], "definition": r[2],
                     "project_slug": r[3]} for r in rows
                ],
            }
        except Exception as e:
            return _safe_error(e)


def _tool_list_projects(user: dict) -> dict:
    """All projects the user can see (owned + shared + super-admin = all)."""
    try:
        from sqlalchemy import create_engine, text
        from sqlalchemy.pool import NullPool

        from db import db_url  # type: ignore

        eng = create_engine(db_url, poolclass=NullPool)
        try:
            with eng.connect() as conn:
                if user.get("is_super"):
                    rows = conn.execute(text(
                        "SELECT slug, name, agent_name, schema_name, updated_at "
                        "FROM public.dash_projects ORDER BY updated_at DESC LIMIT 500"
                    )).fetchall()
                else:
                    rows = conn.execute(text("""
                        SELECT DISTINCT p.slug, p.name, p.agent_name, p.schema_name, p.updated_at
                        FROM public.dash_projects p
                        LEFT JOIN public.dash_project_shares s
                          ON s.project_id = p.id
                        WHERE p.user_id = :uid OR s.shared_with_user_id = :uid
                        ORDER BY p.updated_at DESC LIMIT 500
                    """), {"uid": user["user_id"]}).fetchall()
            return {
                "ok": True,
                "projects": [
                    {"slug": r[0], "name": r[1], "agent_name": r[2],
                     "schema": r[3], "updated_at": str(r[4]) if r[4] else None}
                    for r in rows
                ],
            }
        finally:
            eng.dispose()
    except Exception as e:
        return _safe_error(e)


def _tool_get_project_detail(user: dict, slug: str) -> dict:
    """Codex-enriched metadata: persona, table catalog, doc list,
    available skills, recent training run."""
    if not slug:
        return {"ok": False, "error": "slug required"}
    if not can_access_project(user, slug, "viewer"):
        return {"ok": False, "error": "forbidden"}

    out: dict[str, Any] = {"ok": True, "slug": slug}
    try:
        from sqlalchemy import create_engine, text
        from sqlalchemy.pool import NullPool

        from db import db_url  # type: ignore

        eng = create_engine(db_url, poolclass=NullPool)
        try:
            with eng.connect() as conn:
                row = conn.execute(text(
                    "SELECT name, agent_name, agent_role, schema_name, "
                    "created_at, updated_at "
                    "FROM public.dash_projects WHERE slug = :s"
                ), {"s": slug}).fetchone()
                if not row:
                    return {"ok": False, "error": "not found"}
                out.update({
                    "name": row[0], "agent_name": row[1],
                    "agent_role": row[2], "schema": row[3],
                    "created_at": str(row[4]) if row[4] else None,
                    "updated_at": str(row[5]) if row[5] else None,
                })

                # persona
                persona = conn.execute(text(
                    "SELECT persona FROM public.dash_personas WHERE project_slug = :s"
                ), {"s": slug}).fetchone()
                out["persona"] = persona[0] if persona else None

                # last training run
                tr = conn.execute(text(
                    "SELECT status, finished_at FROM public.dash_training_runs "
                    "WHERE project_slug = :s ORDER BY started_at DESC LIMIT 1"
                ), {"s": slug}).fetchone()
                if tr:
                    out["last_training"] = {
                        "status": tr[0],
                        "finished_at": str(tr[1]) if tr[1] else None,
                    }

                # table catalog (top 20)
                tm = conn.execute(text(
                    "SELECT table_name, metadata FROM public.dash_table_metadata "
                    "WHERE project_slug = :s LIMIT 20"
                ), {"s": slug}).fetchall()
                out["tables"] = [{"name": r[0], "meta": r[1]} for r in tm]
        finally:
            eng.dispose()
    except Exception as e:
        return _safe_error(e)
    return out


def _tool_list_skills(user: dict, project_slug: str) -> dict:
    """Active proven skills for the project (Voyager skill library)."""
    if not project_slug:
        return {"ok": False, "error": "project_slug required"}
    if not can_access_project(user, project_slug, "viewer"):
        return {"ok": False, "error": "forbidden"}
    try:
        from sqlalchemy import create_engine, text
        from sqlalchemy.pool import NullPool

        from db import db_url  # type: ignore

        eng = create_engine(db_url, poolclass=NullPool)
        try:
            with eng.connect() as conn:
                rows = conn.execute(text("""
                    SELECT id, name, description, success_count, avg_judge_score
                    FROM dash.dash_skill_library
                    WHERE project_slug = :s AND status = 'active'
                    ORDER BY success_count * COALESCE(avg_judge_score, 0) DESC
                    LIMIT 50
                """), {"s": project_slug}).fetchall()
            return {
                "ok": True,
                "skills": [
                    {"id": r[0], "name": r[1], "description": r[2],
                     "uses": r[3], "avg_score": float(r[4]) if r[4] is not None else None}
                    for r in rows
                ],
            }
        finally:
            eng.dispose()
    except Exception as e:
        return _safe_error(e)


def _tool_investment_committee_run(
    user: dict,
    project_slug: str,
    symbol: str,
    team_pattern: str = "pipeline",
) -> dict:
    """Run investment-committee analysis for a stock symbol.

    Calls the in-process investment pack (soft-imported). If the pack is
    not installed, returns ``{"ok": False, "error": "investment pack not
    available"}``. Polls run status every 2s for up to 60s; on completion
    returns the verdict + memo summary.
    """
    if not project_slug or not symbol:
        return {"ok": False, "error": "project_slug and symbol required"}
    if not can_access_project(user, project_slug, "editor"):
        return {"ok": False, "error": "forbidden (editor role required)"}

    if team_pattern not in {"coordinate", "pipeline", "broadcast"}:
        team_pattern = "pipeline"

    symbol = str(symbol).strip().upper()

    # Soft-import the investment pack.
    try:
        from dash.verticals.investment.teams.committee import (  # type: ignore
            run_committee,
        )
    except Exception:
        # Fallback to HTTP if a local API path exists.
        try:
            import os
            import time
            import urllib.request
            import urllib.error
            import json as _json

            base = os.getenv("DASH_INTERNAL_API_URL", "http://localhost:8001")
            url = f"{base}/api/projects/{project_slug}/investment/analyze"
            body = _json.dumps(
                {"symbol": symbol, "team_pattern": team_pattern}
            ).encode("utf-8")
            req = urllib.request.Request(
                url, data=body, headers={"Content-Type": "application/json"}
            )
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    payload = _json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as he:
                if he.code == 404:
                    return {"ok": False, "error": "investment pack not available"}
                return {"ok": False, "error": f"http {he.code}: {he.reason}"}
            except Exception:
                return {"ok": False, "error": "investment pack not available"}

            run_id = payload.get("run_id") or payload.get("id")
            if not run_id:
                # No async run — return whatever the endpoint provided.
                return {"ok": True, "result": payload}

            # Poll run status (2s × 30 = 60s cap).
            status_url = (
                f"{base}/api/projects/{project_slug}/investment/runs/{run_id}"
            )
            for _ in range(30):
                time.sleep(2)
                try:
                    with urllib.request.urlopen(status_url, timeout=5) as r2:
                        st = _json.loads(r2.read().decode("utf-8"))
                except Exception:
                    continue
                if (st.get("status") or "").lower() in {"done", "failed", "error"}:
                    return {"ok": True, "result": st}
            return {
                "ok": False,
                "error": "investment run timed out (60s)",
                "run_id": run_id,
            }
        except Exception:
            return {"ok": False, "error": "investment pack not available"}

    # In-process path.
    try:
        result = run_committee(
            project_slug=project_slug,
            symbol=symbol,
            team_pattern=team_pattern,
            user_id=user.get("user_id"),
        )
        return {"ok": True, "result": result}
    except Exception as e:
        return _safe_error(e)


# ---------------------------------------------------------------------------
# Phase 5.2 — apply_skill / run_metric / deep_research
# ---------------------------------------------------------------------------


def _tool_apply_skill_by_query(user: dict, project_slug: str, skill_query: str) -> dict:
    """Match an active skill by name/description ILIKE, then execute via
    ``dash.tools.apply_skill``. Editor role required (skill exec mutates
    usage counters)."""
    if not project_slug or not skill_query:
        return {"ok": False, "error": "project_slug and skill_query required"}
    if not can_access_project(user, project_slug, "editor"):
        return {"ok": False, "error": "forbidden (editor role required)"}
    try:
        from sqlalchemy import text as _text

        from db.session import get_sql_engine  # type: ignore

        with get_sql_engine().connect() as conn:
            row = conn.execute(_text(
                "SELECT id, name FROM public.dash_skill_library "
                "WHERE project_slug = :p AND status = 'active' "
                "AND (name ILIKE :q OR description ILIKE :q) "
                "ORDER BY success_count DESC NULLS LAST LIMIT 1"
            ), {"p": project_slug, "q": f"%{skill_query}%"}).fetchone()
        if not row:
            return {"ok": False, "error": f"no active skill matched: {skill_query}"}
        from dash.tools.apply_skill import apply_skill as _apply_skill  # type: ignore

        out = _apply_skill(skill_id=int(row[0]), params={})
        import json as _json
        try:
            parsed = _json.loads(out) if isinstance(out, str) else out
        except Exception:
            parsed = {"raw": str(out)[:8000]}
        return {"ok": True, "skill_id": int(row[0]), "skill_name": row[1], "result": parsed}
    except Exception as e:
        return _safe_error(e)


def _tool_run_metric(user: dict, project_slug: str, metric_name: str,
                     filters: dict | None = None) -> dict:
    """Run a named metric via ``dash.tools.metric_compiler.run_metric``."""
    if not project_slug or not metric_name:
        return {"ok": False, "error": "project_slug and metric_name required"}
    if not can_access_project(user, project_slug, "viewer"):
        return {"ok": False, "error": "forbidden"}
    try:
        from dash.tools.metric_compiler import (  # type: ignore
            load_definition,
            run_metric as _run_metric,
        )
        spec = load_definition(project_slug, metric_name)
        if not spec:
            return {"ok": False, "error": f"metric '{metric_name}' not found"}
        extra = [
            {"column": str(c), "op": "=", "value": v}
            for c, v in (filters or {}).items()
        ] if isinstance(filters, dict) else None
        res = _run_metric(
            project_slug=project_slug,
            spec=spec,
            extra_filters=extra,
        )
        return res if isinstance(res, dict) else {"ok": True, "result": res}
    except Exception as e:
        return _safe_error(e)


def _tool_deep_research(user: dict, project_slug: str, question: str) -> dict:
    """Run 9-stage Deep Research; strip pdf_bytes from return."""
    if not project_slug or not question:
        return {"ok": False, "error": "project_slug and question required"}
    if not can_access_project(user, project_slug, "editor"):
        return {"ok": False, "error": "forbidden (editor role required)"}
    try:
        from dash.tools.deep_research import DeepResearch  # type: ignore

        out = DeepResearch(project_slug=project_slug).run(question, project_slug)
        if not isinstance(out, dict):
            return {"ok": True, "result": str(out)[:8000]}
        pdf_bytes = out.pop("pdf_bytes", None)
        if pdf_bytes:
            out["pdf_size_bytes"] = len(pdf_bytes)
            run_id = out.get("run_id") or (out.get("spec", {}) or {}).get("run_id")
            out["pdf_url"] = f"/api/research/{run_id}/pdf" if run_id else None
        return {"ok": True, "result": out}
    except Exception as e:
        return _safe_error(e)


def _tool_list_dashboards(user: dict, project_slug: str) -> dict:
    if not project_slug:
        return {"ok": False, "error": "project_slug required"}
    if not can_access_project(user, project_slug, "viewer"):
        return {"ok": False, "error": "forbidden"}
    try:
        from sqlalchemy import create_engine, text
        from sqlalchemy.pool import NullPool

        from db import db_url  # type: ignore

        eng = create_engine(db_url, poolclass=NullPool)
        try:
            with eng.connect() as conn:
                rows = conn.execute(text(
                    "SELECT id, name, jsonb_array_length(widgets) AS n_widgets, updated_at "
                    "FROM public.dash_dashboards WHERE project_slug = :s "
                    "ORDER BY updated_at DESC LIMIT 100"
                ), {"s": project_slug}).fetchall()
            return {
                "ok": True,
                "dashboards": [
                    {"id": r[0], "name": r[1], "widgets": r[2],
                     "updated_at": str(r[3]) if r[3] else None}
                    for r in rows
                ],
            }
        finally:
            eng.dispose()
    except Exception as e:
        return _safe_error(e)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

REGISTRY: list[dict[str, Any]] = [
    {
        "name": "dash_sql_query",
        "description": (
            "Run a read-only SQL query (SELECT/WITH only) against a "
            "Dash project's PostgreSQL schema. Uses the project's "
            "read-only engine with RLS + transaction_read_only enforced. "
            "Result is capped at `limit` rows (default 500, max 5000)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_slug": {"type": "string", "description": "Project slug"},
                "sql": {"type": "string", "description": "SELECT or WITH statement"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 5000, "default": 500},
            },
            "required": ["project_slug", "sql"],
        },
        "handler": _tool_sql_query,
        "requires": "viewer",
    },
    {
        "name": "dash_recall",
        "description": (
            "Unified semantic + keyword recall across the project's "
            "knowledge base, Company Brain, knowledge graph, and "
            "grounded facts. Returns top_k results with source + score."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_slug": {"type": "string"},
                "q": {"type": "string", "description": "Search query"},
                "top_k": {"type": "integer", "minimum": 1, "maximum": 50, "default": 8},
            },
            "required": ["project_slug", "q"],
        },
        "handler": _tool_recall,
        "requires": "viewer",
    },
    {
        "name": "dash_apply_skill",
        "description": (
            "Execute a proven skill from the Voyager skill library "
            "(dash.dash_skill_library) for the given project. Requires "
            "editor role on the project."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_slug": {"type": "string"},
                "skill_id": {"type": "string"},
                "params": {"type": "object", "additionalProperties": True},
            },
            "required": ["project_slug", "skill_id"],
        },
        "handler": _tool_apply_skill,
        "requires": "editor",
    },
    {
        "name": "dash_search_brain",
        "description": (
            "Search the Company Brain (glossary, formulas, aliases, "
            "patterns, org, thresholds). Scope: 'global' (default), "
            "'project' (requires project_slug), or 'personal'."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "scope": {
                    "type": "string",
                    "enum": ["global", "project", "personal"],
                    "default": "global",
                },
                "project_slug": {"type": "string"},
            },
            "required": ["query"],
        },
        "handler": _tool_search_brain,
        "requires": None,
    },
    {
        "name": "dash_list_projects",
        "description": "List projects the authenticated user can access (owner + shared).",
        "inputSchema": {"type": "object", "properties": {}},
        "handler": _tool_list_projects,
        "requires": None,
    },
    {
        "name": "dash_get_project_detail",
        "description": (
            "Codex-enriched project metadata: name, agent, persona, "
            "schema, recent training run, top 20 table summaries."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"slug": {"type": "string"}},
            "required": ["slug"],
        },
        "handler": _tool_get_project_detail,
        "requires": "viewer",
    },
    {
        "name": "dash_list_skills",
        "description": "Active skills (Voyager library) available for the project.",
        "inputSchema": {
            "type": "object",
            "properties": {"project_slug": {"type": "string"}},
            "required": ["project_slug"],
        },
        "handler": _tool_list_skills,
        "requires": "viewer",
    },
    {
        "name": "dash_investment_committee_run",
        "description": (
            "Run investment committee analysis on a stock symbol. Returns "
            "BUY/HOLD/PASS verdict + conviction + reasoning. team_pattern: "
            "coordinate (default, dynamic routing), pipeline (5-step "
            "deterministic), broadcast (parallel all agents)"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_slug": {"type": "string"},
                "symbol": {
                    "type": "string",
                    "description": "Stock ticker symbol (e.g. AAPL, NVDA)",
                },
                "team_pattern": {
                    "type": "string",
                    "enum": ["coordinate", "pipeline", "broadcast"],
                    "default": "pipeline",
                },
            },
            "required": ["project_slug", "symbol"],
        },
        "handler": _tool_investment_committee_run,
        "requires": "editor",
    },
    {
        "name": "dash_apply_skill_by_query",
        "description": (
            "Match a skill in dash_skill_library by name/description ILIKE "
            "and execute it via dash.tools.apply_skill. Editor role required."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_slug": {"type": "string"},
                "skill_query": {"type": "string", "description": "Name/keyword to match"},
            },
            "required": ["project_slug", "skill_query"],
        },
        "handler": _tool_apply_skill_by_query,
        "requires": "editor",
    },
    {
        "name": "dash_run_metric",
        "description": (
            "Execute a named metric from dash_metric_definitions via the "
            "metric_compiler engine. Returns rows + total + SQL."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_slug": {"type": "string"},
                "metric_name": {"type": "string"},
                "filters": {"type": "object", "additionalProperties": True},
            },
            "required": ["project_slug", "metric_name"],
        },
        "handler": _tool_run_metric,
        "requires": "viewer",
    },
    {
        "name": "dash_deep_research",
        "description": (
            "Run the 9-stage Deep Research pipeline (scope → hypothesis → "
            "plan SQL → parallel exec → evidence ranking → synthesis → "
            "cross-check → recommendation → render). PDF bytes stripped "
            "from response; pdf_url returned when persisted."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_slug": {"type": "string"},
                "question": {"type": "string"},
            },
            "required": ["project_slug", "question"],
        },
        "handler": _tool_deep_research,
        "requires": "editor",
    },
    {
        "name": "dash_list_dashboards",
        "description": "List dashboards saved for the project (id, name, widget count).",
        "inputSchema": {
            "type": "object",
            "properties": {"project_slug": {"type": "string"}},
            "required": ["project_slug"],
        },
        "handler": _tool_list_dashboards,
        "requires": "viewer",
    },
]


_BY_NAME: dict[str, dict[str, Any]] = {t["name"]: t for t in REGISTRY}


def list_tools() -> list[dict[str, Any]]:
    """Public-facing tool descriptors (no handlers, JSON-serializable)."""
    return [
        {"name": t["name"], "description": t["description"],
         "inputSchema": t["inputSchema"]}
        for t in REGISTRY
    ]


def call_tool(name: str, user: dict, arguments: dict | None = None) -> dict[str, Any]:
    """Look up + invoke a tool. Returns whatever the handler returns
    (always a dict). Raises ``KeyError`` if the tool name is unknown."""
    tool = _BY_NAME.get(name)
    if tool is None:
        raise KeyError(name)
    args = arguments or {}
    handler = tool["handler"]
    try:
        return handler(user, **args)
    except TypeError as e:
        return {"ok": False, "error": f"bad arguments: {e}"}
    except Exception as e:
        return _safe_error(e)
