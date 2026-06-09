"""The Engineer — an Agno agent that DESIGNS a materialized-view semantic layer.

It inspects the trained tables (schema, column roles, discovered relationships,
tiny samples) and proposes matviews that make common reads fast and correct. It
NEVER executes DDL: it returns a structured plan, which trusted Python
(dash.training.semantic_layer) whitelist-validates and applies.

Tools handed to the agent are READ-ONLY (schema/relationships/samples) plus a
dry-run EXPLAIN it can iterate against. There is deliberately no "run SQL" or
"create" tool — the model can never hold an executed string except the SELECT
body, which the whitelist vets.
"""
from __future__ import annotations

import logging
from typing import List

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ── Structured output the agent must return ───────────────────────────────────
class MatviewProposal(BaseModel):
    name: str = Field(description="snake_case [a-z][a-z0-9_]*, must not match a base table")
    purpose: str = Field(description="one line: why this view speeds reads")
    grain: str = Field(description="one row per … (the view's grain)")
    select_sql: str = Field(description="the SELECT body only — pure read, single statement, project schema only")
    unique_index: str = Field(description="comma-separated columns for the unique index (required for REFRESH CONCURRENTLY)")
    extra_indexes: List[str] = Field(default_factory=list, description="optional extra index column-lists")


class SemanticLayerPlan(BaseModel):
    matviews: List[MatviewProposal] = Field(default_factory=list)
    skipped: List[str] = Field(default_factory=list, description="ideas considered but dropped, with reason")


ENGINEER_RULES = """You are the Engineer — a senior data engineer designing a SQL semantic layer
(PostgreSQL materialized views) over a project's trained tables. Your matviews
pre-compute joins/aggregates so the chat agent reads fast and correct.

HARD RULES (a view that breaks any of these is useless and will be rejected):
1. select_sql is ONE pure SELECT (or WITH…SELECT). No DROP/ALTER/INSERT/UPDATE/
   DELETE/CREATE/GRANT, no second statement, no SQL comments.
2. Reference ONLY the project's own tables, unqualified (search_path resolves
   them). Never touch pg_catalog, information_schema, public, dash, ai.
3. Code/ID joins: normalize BOTH sides — cast to ::text (e.g.
   a.article_code::text = b.article_code::text). Numeric codes stored as text
   can carry scientific notation (1.2E+12); a raw join silently mismatches.
4. Keep unmatched rows visible: use FULL JOIN (or LEFT) + a boolean `linked`
   flag rather than dropping orphans.
5. Aggregate measures with SUM()+GROUP BY — never expect row-level callers to
   sum. Name aggregates clearly (stock_qty, total_value).
6. Every matview MUST declare a unique_index (a column set unique per row) —
   it is required for REFRESH MATERIALIZED VIEW CONCURRENTLY.
7. Propose only views that speed REAL questions. Quality over quantity, max 5.

WORKFLOW:
- Call inspect_schema and get_relationships to see tables, column roles, and
  verified join keys.
- Use sample_rows to check real value formats before writing a join.
- Validate every SELECT with dry_run_sql (EXPLAIN) and fix errors BEFORE
  returning it. Do not return a SELECT that failed dry_run_sql.
- Return a SemanticLayerPlan: the matviews you verified, plus skipped ideas
  with reasons.
"""


def _make_tools(slug: str, schema: str, db_url: str):
    """Read-only tools bound to this project. dry_run_sql runs EXPLAIN only."""
    from agno.tools import tool
    from sqlalchemy import text
    from dash.training.semantic_layer import _timeout_engine

    def _eng():
        return _timeout_engine(db_url)

    @tool(name="inspect_schema",
          description="List the project's tables with their columns and inferred roles (id/dimension/measure/temporal). No args.")
    def inspect_schema() -> str:
        import json
        out = {}
        try:
            eng = _eng()
            with eng.connect() as c:
                c.execute(text(f"SET search_path = {schema}, public"))
                tbls = [r[0] for r in c.execute(text("""
                    SELECT table_name FROM information_schema.tables
                    WHERE table_schema = :s AND table_type = 'BASE TABLE'
                    ORDER BY table_name
                """), {"s": schema}).fetchall()]
                meta = {r[0]: (r[1] if isinstance(r[1], dict) else {}) for r in c.execute(text("""
                    SELECT table_name, metadata FROM public.dash_table_metadata
                    WHERE project_slug = :s
                """), {"s": slug}).fetchall()}
                for t in tbls:
                    cols = c.execute(text("""
                        SELECT column_name, data_type FROM information_schema.columns
                        WHERE table_schema = :s AND table_name = :t ORDER BY ordinal_position
                    """), {"s": schema, "t": t}).fetchall()
                    roles = {}
                    pv = (meta.get(t) or {}).get("profile_v2") or {}
                    if isinstance(pv, dict):
                        for col, info in (pv.get("columns") or {}).items():
                            if isinstance(info, dict) and info.get("role"):
                                roles[col] = info["role"]
                    out[t] = [{"col": cn, "type": dt, "role": roles.get(cn, "")}
                              for cn, dt in cols]
        except Exception as e:
            return f"inspect_schema error: {e}"
        return json.dumps(out)[:6000]

    @tool(name="get_relationships",
          description="List verified join keys between tables (from_table.from_column → to_table.to_column with confidence). No args.")
    def get_relationships() -> str:
        import json
        rels = []
        try:
            eng = _eng()
            with eng.connect() as c:
                for r in c.execute(text("""
                    SELECT from_table, from_column, to_table, to_column, confidence
                    FROM public.dash_relationships WHERE project_slug = :s
                    ORDER BY confidence DESC
                """), {"s": slug}).fetchall():
                    rels.append({"from": f"{r[0]}.{r[1]}", "to": f"{r[2]}.{r[3]}",
                                 "confidence": float(r[4] or 0)})
        except Exception as e:
            return f"get_relationships error: {e}"
        return json.dumps(rels)[:4000]

    @tool(name="sample_rows",
          description="Return up to 5 sample rows from a table to inspect real value formats. Args: table (str).")
    def sample_rows(table: str) -> str:
        import json, re as _re
        t = _re.sub(r"[^a-z0-9_]", "", str(table).lower())[:63]
        if not t:
            return "sample_rows error: bad table name"
        try:
            eng = _eng()
            with eng.connect() as c:
                c.execute(text(f"SET search_path = {schema}, public"))
                rows = c.execute(text(f'SELECT * FROM {schema}."{t}" LIMIT 5')).mappings().all()
            return json.dumps([dict(r) for r in rows], default=str)[:4000]
        except Exception as e:
            return f"sample_rows error: {str(e)[:200]}"

    @tool(name="dry_run_sql",
          description="EXPLAIN a candidate SELECT (does NOT execute it) to confirm it is valid before proposing. Args: select_sql (str).")
    def dry_run_sql(select_sql: str) -> str:
        sql = (select_sql or "").strip().rstrip(";")
        if not sql:
            return "dry_run error: empty"
        try:
            eng = _eng()
            with eng.connect() as c:
                c.execute(text(f"SET search_path = {schema}, public"))
                plan = c.execute(text(f"EXPLAIN {sql}")).fetchall()
            return "OK — plan:\n" + "\n".join(str(p[0]) for p in plan[:8])
        except Exception as e:
            return f"INVALID — fix and retry: {str(e)[:300]}"

    return [inspect_schema, get_relationships, sample_rows, dry_run_sql]


def design_semantic_layer(slug: str, schema: str, db_url: str):
    """Run the Engineer agent and return its SemanticLayerPlan (or None)."""
    try:
        from agno.agent import Agent
        from agno.tools.reasoning import ReasoningTools
        from agno.models.openrouter import OpenRouter
        from dash.settings import get_deep_model, OR_DATA_POLICY
    except Exception as e:
        logger.warning("engineer_agent: agno import failed: %s", e)
        return None

    tools = _make_tools(slug, schema, db_url)
    tools.append(ReasoningTools())
    engineer = Agent(
        id="engineer", name="Engineer",
        role="Designs a SQL semantic layer (materialized views) over trained tables.",
        model=OpenRouter(id=get_deep_model(), temperature=0.1, extra_body=OR_DATA_POLICY),
        tools=tools,
        output_schema=SemanticLayerPlan,
        instructions=ENGINEER_RULES,
        markdown=False,
    )
    prompt = (
        "Design the materialized-view semantic layer for this project. Inspect "
        "the schema and relationships, sample rows where needed, dry-run every "
        "SELECT, then return your SemanticLayerPlan. Focus on the joins and "
        "aggregates the chat agent will hit most."
    )
    try:
        resp = engineer.run(prompt)
        plan = getattr(resp, "content", None)
        if isinstance(plan, SemanticLayerPlan):
            return plan
        if isinstance(plan, dict):
            return SemanticLayerPlan(**plan)
    except Exception as e:
        logger.warning("engineer_agent: run failed for %s: %s", slug, e)
    return None
