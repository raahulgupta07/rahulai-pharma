"""Minimal LLM dashboard planner — signals + 1 LLM call → DashboardSpec."""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from sqlalchemy import text

from dash.dashboards.lint import lint_spec
from dash.dashboards.spec import DashboardSpec
from dash.dashboards.templates import TEMPLATES, pick_template

logger = logging.getLogger(__name__)


def _real_tables(project_slug: str) -> list[dict]:
    """Query the project's actual readonly engine for REAL tables in its search_path.

    This is authoritative — only returns tables that actually exist.
    """
    from db.session import get_project_readonly_engine
    result: list[dict] = []
    try:
        eng = get_project_readonly_engine(project_slug)
        with eng.connect() as conn:
            # Prefer project's own schema; exclude system schemas, public (dash_* infra), and ai (agno infra)
            rows = conn.execute(text("""
                SELECT table_schema, table_name
                FROM information_schema.tables
                WHERE table_type='BASE TABLE'
                  AND table_schema NOT IN ('pg_catalog','information_schema','public','ai')
                ORDER BY
                  CASE WHEN table_schema = :slug THEN 0 ELSE 1 END,
                  table_schema, table_name
                LIMIT 50
            """), {"slug": project_slug}).fetchall()
            for sch, name in rows:
                cols = []
                try:
                    crows = conn.execute(text("""
                        SELECT column_name, data_type FROM information_schema.columns
                        WHERE table_schema=:s AND table_name=:n LIMIT 20
                    """), {"s": sch, "n": name}).fetchall()
                    cols = [(c[0], c[1]) for c in crows]
                except Exception:
                    pass
                sample = []
                try:
                    srows = conn.execute(text(f'SELECT * FROM "{sch}"."{name}" LIMIT 2')).fetchall()
                    sample = [dict(r._mapping) for r in srows]
                except Exception:
                    pass
                result.append({
                    "schema": sch,
                    "name": name,
                    "qualified": f'"{sch}"."{name}"',
                    "table_name": name,
                    "cols": cols,
                    "sample_rows": sample,
                })
    except Exception as e:
        logger.debug(f"_real_tables failed for {project_slug}: {e}")
    return result


def _collect_signals(project_slug: str) -> dict:
    from dash.tools.skill_refinery import _get_engine

    sig: dict[str, Any] = {"persona": "", "tables": [], "kg_entities": []}

    # Fetch REAL tables from project DB — authoritative
    real = _real_tables(project_slug)

    # Pull purpose text from dash_table_metadata (join by name)
    purpose_by_name: dict[str, str] = {}
    try:
        eng = _get_engine()
        with eng.connect() as conn:
            row = conn.execute(text(
                "SELECT agent_role, agent_personality FROM public.dash_projects WHERE slug=:s"
            ), {"s": project_slug}).fetchone()
            if row:
                sig["persona"] = " · ".join([x for x in (row[0], row[1]) if x])

            mrows = conn.execute(text(
                "SELECT table_name, metadata FROM public.dash_table_metadata "
                "WHERE project_slug=:s LIMIT 100"
            ), {"s": project_slug}).fetchall()
            for tbl, meta in mrows:
                if isinstance(meta, dict):
                    p = str(meta.get("purpose") or meta.get("description") or "")[:200]
                    if p:
                        purpose_by_name[tbl] = p

            kgrows = conn.execute(text(
                "SELECT subject, COUNT(*) c FROM public.dash_knowledge_triples "
                "WHERE project_slug=:s GROUP BY subject ORDER BY c DESC LIMIT 10"
            ), {"s": project_slug}).fetchall()
            sig["kg_entities"] = [r[0] for r in kgrows if r and r[0]]
    except Exception as e:
        logger.debug(f"planner signal collection (purpose/kg) failed: {e}")

    # Attach purpose to real tables
    for t in real:
        t["purpose"] = purpose_by_name.get(t["name"], "")
        t["sample_cols"] = [c[0] for c in t["cols"][:15]]

    sig["tables"] = real
    return sig


def _strip_fences(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    m = re.search(r"\{.*\}", s, re.DOTALL)
    return m.group(0) if m else s


def _parse_json_robust(raw: str) -> dict | None:
    text = (raw or "").strip()
    # tier 1: direct parse
    try:
        return json.loads(text)
    except Exception:
        pass
    # tier 2: extract first {...} block
    m = re.search(r'\{[\s\S]*\}', text)
    if m:
        candidate = m.group(0)
        try:
            return json.loads(candidate)
        except Exception:
            # tier 3: fix common issues
            fixed = re.sub(r',(\s*[}\]])', r'\1', candidate)
            fixed = re.sub(r'//.*?$', '', fixed, flags=re.MULTILINE)
            fixed = re.sub(r'/\*[\s\S]*?\*/', '', fixed)
            try:
                return json.loads(fixed)
            except Exception:
                pass
    # tier 4: try json-repair lib if installed
    try:
        from json_repair import repair_json
        return json.loads(repair_json(text))
    except Exception:
        pass
    return None


_PROMPT = """You are DASHBOARD-PLANNER for a Dash data agent.

Generate a dashboard spec as JSON.

PROJECT: {slug}
PERSONA: {persona}

EXACT TABLES (use these literal names — DO NOT modify, pluralize, or invent): {table_names}

SQL RULES:
- ALWAYS qualify with schema: "schema"."table" or schema.table_name
- NEVER use a table not listed above
- NEVER invent column names — use only listed ones
- Do not append 'table' or 's'. Do not pluralize. Use exact spelling shown.

TABLES:
{tables}
KG ENTITIES: {kg}

USER PROMPT: {prompt}

USER PREFERENCES (learned from past dashboards): prefer={pref_charts}, avoid={avoid_charts}, prefer_insights={pref_insights}. Honor these unless user prompt explicitly contradicts.

CHAT CONTEXT (already-explored questions/sql/insights — reuse, don't re-derive):
{chat_context}

QUESTION COUNT: {n_questions} — minimum cells required = max({n_questions}, 6).

LAYOUT TEMPLATE: {template_name}
Template description: {template_desc}
Grid slots (use these EXACT positions, map your cells to roles):
{template_grid}

Output ONLY a JSON object, no markdown fences, with EXACT fields:
- id (string slug)
- title (string)
- project_slug (string = "{slug}")
- persona (string)
- template (string = "{template_name}")
- filters (list of {{col, type in [daterange|multi|single], default}})
- cells (list of {{id, type in [kpi|chart|table|insight], grid:[col,row,w,h] in 12-col system, title, config:{{}}}})

Rules: Map each template slot to one cell, copying its grid array exactly. role=kpi→type=kpi, role=trend/mix→type=chart, role=table→type=table, role=alerts→type=insight, role=filters→type=table. You still pick chart types, SQL, and titles. No overlapping grids. No pie >7 slices.

CRITICAL OUTPUT RULES:
- Output ONLY a single JSON object. No markdown fences, no preamble, no comments.
- No trailing commas. Use double quotes only.
- All strings on single line (escape newlines).
- Required keys: id, title, project_slug, persona, filters, cells, theme.
- Each cell: {{id, type: "kpi"|"chart"|"table"|"insight", grid: [col,row,w,h], title, config: {{}}}}
- grid array MUST be exactly 4 integers, col 0-11, row 0-11, w 1-12, h 1-8.
- Every KPI/CHART/TABLE cell MUST have config.sql = "SELECT ..." (real SQL against the project's tables).
- KPI sql returns SINGLE scalar (one row, one column).
- CHART sql returns 2-3 columns: x, y, optional series.
- TABLE sql returns up to 50 rows.
- Use only tables and columns shown in TABLES signal. Use proper JOINs.
- For each chart, also set config.chart_type to "line" | "bar" | "pie" | "scatter" | "area".
- For each chart, set config.x_col, config.y_col, config.series_col (if applicable).
- Output starts with {{ and ends with }}. Nothing else.
- MINIMUM cells = max(N_questions, 6). One KPI/chart per question topic. Up to 20 cells allowed.
- Reuse PRIOR SQLs verbatim when relevant (set config.sql to the prior SQL).
- Cell title is a short human label only (e.g. "Total Stock Units", "Revenue by Region"). NEVER prefix with "Q1:", "Q2:", "Q3:" or any question numbers.
"""


def generate_spec(
    project_slug: str,
    prompt: str,
    persona: str = "",
    chat_context: dict | None = None,
    user_id: str | None = None,
) -> dict:
    prefs = {"preferred_chart_types": [], "avoid_chart_types": [], "preferred_insight_types": []}
    if user_id:
        try:
            from dash.dashboards.memory import get_preferences
            prefs = get_preferences(user_id, project_slug)
        except Exception as e:
            logger.debug(f"prefs lookup failed: {e}")
    signals = _collect_signals(project_slug)
    if persona:
        signals["persona"] = persona

    def _fmt_table(t: dict) -> str:
        cols = t.get("cols") or []
        cols_str = ", ".join(f"{c[0]}:{c[1]}" if c[1] else f"{c[0]}" for c in cols) if cols else ""
        cols_part = f" cols=[{cols_str}]" if cols_str else ""
        purpose = t.get("purpose", "")
        purpose_part = f" purpose: {purpose}" if purpose else ""
        samples = t.get("sample_rows") or []
        sample_part = ""
        if samples:
            try:
                sample_part = f" sample={json.dumps(samples, default=str)[:400]}"
            except Exception:
                sample_part = ""
        qualified = t.get("qualified") or f'"{t.get("table_name","")}"'
        return f'- {qualified}{cols_part}{purpose_part}{sample_part}'

    tables_str = "\n".join(_fmt_table(t) for t in signals["tables"])[:6000] or "(none)"
    table_names_str = ", ".join(t.get("qualified") or f'"{t["table_name"]}"' for t in signals["tables"]) or "(none)"
    kg_str = ", ".join(signals["kg_entities"])[:400] or "(none)"
    ctx_str = "(none)"
    n_questions = 0
    if chat_context:
        qs = chat_context.get("questions") or []
        sqls = chat_context.get("sqls") or []
        insights = chat_context.get("insights") or []
        prior_results = chat_context.get("prior_results") or []
        n_questions = len(qs)

        parts: list[str] = []
        parts.append(f"USER ASKED THESE QUESTIONS IN THIS THREAD ({len(qs)} total):")
        for i, q in enumerate(qs[:30], 1):
            parts.append(f"{i}. {str(q)[:400]}")

        if sqls:
            parts.append("")
            parts.append("PRIOR SQLs ALREADY RUN (reuse if relevant):")
            for s in sqls[:30]:
                parts.append(f"- {str(s)[:200]}")

        if prior_results:
            parts.append("")
            parts.append("PRIOR RESULTS SAMPLES:")
            for pr in prior_results[:15]:
                sql_snip = str(pr.get("sql", ""))[:80]
                sample_snip = json.dumps(pr.get("sample", []), default=str)[:300]
                parts.append(f"- query: {sql_snip} -> {sample_snip}")

        if insights:
            parts.append("")
            parts.append("INSIGHTS ALREADY SURFACED:")
            for ins in insights[:10]:
                parts.append(f"- {str(ins)[:200]}")

        ctx_str = "\n".join(parts)[:8000]

    num_insights = len((chat_context or {}).get("insights") or [])
    template_name = pick_template(prompt, signals["persona"], num_insights)
    template = TEMPLATES[template_name]
    template_grid_str = json.dumps(template["grid"])

    full_prompt = _PROMPT.format(
        slug=project_slug,
        persona=signals["persona"] or "(none)",
        tables=tables_str,
        table_names=table_names_str,
        kg=kg_str,
        prompt=prompt[:1000],
        chat_context=ctx_str,
        n_questions=max(n_questions, 6),
        template_name=template_name,
        template_desc=template["description"],
        template_grid=template_grid_str,
        pref_charts=prefs.get("preferred_chart_types") or [],
        avoid_charts=prefs.get("avoid_chart_types") or [],
        pref_insights=prefs.get("preferred_insight_types") or [],
    )

    try:
        from dash.settings import training_llm_call
        try:
            raw = training_llm_call(full_prompt, task="dashboard_gen")
        except Exception:
            raw = training_llm_call(full_prompt, task="deep_analysis")
    except Exception as e:
        return {"ok": False, "error": f"LLM call failed: {e}"}

    if not raw:
        return {"ok": False, "error": "empty LLM response"}

    stripped = _strip_fences(raw)
    parsed = _parse_json_robust(stripped)
    if parsed is None:
        return {"ok": False, "error": f"json parse failed: {raw[:200]}"}
    try:
        parsed.setdefault("project_slug", project_slug)
        parsed.setdefault("persona", signals["persona"])
        parsed.setdefault("template", template_name)
        parsed.setdefault("cells", [])
        parsed.setdefault("filters", [])
        parsed.setdefault("insights", [])
        parsed.setdefault("id", f"dash_{int(time.time())}")
        parsed.setdefault("title", (prompt[:50] if prompt else "Dashboard"))
        # Coerce non-string fields the LLM sometimes returns as dicts/lists
        if isinstance(parsed.get("theme"), dict):
            parsed["theme"] = str(parsed["theme"].get("mode", "light"))
        if not isinstance(parsed.get("template"), str):
            parsed["template"] = template_name
        if not isinstance(parsed.get("persona"), str):
            parsed["persona"] = str(parsed.get("persona", ""))
        spec = DashboardSpec(**parsed)
    except Exception as e:
        return {"ok": False, "error": f"validate failed: {str(e)[:300]}", "raw_parsed": parsed}

    warnings = lint_spec(spec)
    return {"ok": True, "spec": spec.model_dump(mode="json"), "warnings": warnings}
