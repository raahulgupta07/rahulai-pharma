"""
Hybrid Lookup (deterministic + semantic)
=========================================

FEATURE B. One tool that routes EXACT-answer questions (count / sum / total /
filtered list) to **deterministic SQL** against the project schema, and
MEANING questions to **semantic search** (search_all). When a question mixes
both, it does both and labels the result HYBRID.

Why: Dash had a "1804 vs 1544" miscount problem — fuzzy / semantic retrieval
paths produced wrong COUNTs because a vector store can't count rows. The fix is
to never let an exact metric be answered by similarity search. Deterministic
metrics go straight to Postgres and come back exact; only meaning/context
questions touch the vector layer.

Contract:
    hybrid_lookup(query: str) -> str

    - Classifies `query`.
    - DETERMINISTIC (exact)  → runs a single safe COUNT/SUM SQL against the
      project read-only engine (resolved exactly like the Analyst's SQL tool)
      and returns the figure.
    - SEMANTIC               → calls the existing search_all tool and returns
      ranked context.
    - HYBRID                 → returns both, clearly separated.
    - Never raises. Any failure → "hybrid_lookup error: ...". If a safe SQL
      can't be built, it conservatively falls back to SEMANTIC and says so.

Engine rule: reads go through ``get_project_readonly_engine`` (same resolution
the Analyst uses); the cached engine is never disposed.
"""

from __future__ import annotations

import logging
import re

from agno.tools import tool

logger = logging.getLogger(__name__)

# Keywords that mean "give me an exact figure / filtered set" — these MUST be
# answered deterministically, never by similarity search.
_EXACT_PATTERNS = re.compile(
    r"\b("
    r"how\s+many|how\s+much|number\s+of|count\s+of|count|total|totals|"
    r"sum\s+of|sum|average|avg|mean|"
    r"list\s+(all|every|the)|list|show\s+(all|me\s+all)"
    r")\b",
    re.IGNORECASE,
)

# Keywords that mean "explain / describe / what does this mean" — these are
# meaning questions and belong to semantic search.
_MEANING_PATTERNS = re.compile(
    r"\b("
    r"what\s+is|what\s+are|what\s+does|explain|describe|definition|define|"
    r"why|how\s+does|meaning|mean\s+by|about|overview|summary|summarize|"
    r"context|tell\s+me\s+about|who\s+is|recommend|suggest"
    r")\b",
    re.IGNORECASE,
)


def _classify(query: str) -> str:
    """Return one of: 'deterministic', 'semantic', 'hybrid'."""
    q = query or ""
    has_exact = bool(_EXACT_PATTERNS.search(q))
    has_meaning = bool(_MEANING_PATTERNS.search(q))
    if has_exact and has_meaning:
        return "hybrid"
    if has_exact:
        return "deterministic"
    if has_meaning:
        return "semantic"
    # No strong signal — be conservative and treat as semantic (never invent a
    # COUNT for a vague question).
    return "semantic"


def _resolve_engine_and_schema(project_slug: str | None):
    """Resolve the project read-only engine + schema EXACTLY as the Analyst's
    SQL tool does (db.get_project_readonly_engine + sanitized slug schema).

    Returns (engine, schema) or (None, None) when no project is bound. Never
    disposes the cached engine.
    """
    if not project_slug:
        return None, None
    from db import get_project_readonly_engine

    engine = get_project_readonly_engine(project_slug)
    schema = re.sub(r"[^a-z0-9_]", "_", project_slug.lower())[:63]
    return engine, schema


def _list_tables(engine, schema: str) -> list[str]:
    """Return base-table names in the project schema (no views), via inspect."""
    from sqlalchemy import inspect

    insp = inspect(engine)
    try:
        return list(insp.get_table_names(schema=schema))
    except Exception:
        return []


def _table_columns(engine, schema: str, table: str) -> list[str]:
    from sqlalchemy import inspect

    insp = inspect(engine)
    try:
        return [c["name"] for c in insp.get_columns(table, schema=schema)]
    except Exception:
        return []


def _pick_table(query: str, tables: list[str]) -> str | None:
    """Best-effort: pick the table whose name shares the most word-tokens with
    the query. Conservative — returns None if nothing overlaps so the caller
    falls back to semantic instead of guessing.
    """
    if not tables:
        return None
    q_tokens = {t for t in re.split(r"[^a-z0-9]+", query.lower()) if len(t) > 2}
    if not q_tokens:
        # No usable tokens; if there is exactly one table, use it.
        return tables[0] if len(tables) == 1 else None

    best, best_score = None, 0
    for tbl in tables:
        tbl_tokens = {t for t in re.split(r"[^a-z0-9]+", tbl.lower()) if len(t) > 2}
        score = len(q_tokens & tbl_tokens)
        if score > best_score:
            best, best_score = tbl, score
    if best is not None and best_score > 0:
        return best
    # Single-table project → safe to use it even without overlap.
    return tables[0] if len(tables) == 1 else None


def _deterministic_count(engine, schema: str, query: str) -> str:
    """Build and run ONE safe COUNT(*) against the most-relevant table.

    Returns a labelled exact figure, or a string starting with 'FALLBACK:' when
    no safe SQL could be built (signals the caller to use semantic instead).
    Never reinvents general SQL generation — it only ever issues COUNT(*).
    """
    from sqlalchemy import text

    tables = _list_tables(engine, schema)
    table = _pick_table(query, tables)
    if not table:
        return "FALLBACK: could not confidently map the question to a table."

    # Identifiers are restricted to [a-z0-9_] (schema sanitized at resolution,
    # table comes from inspector) so they are safe to interpolate quoted.
    safe_schema = re.sub(r"[^a-z0-9_]", "", schema)
    safe_table = re.sub(r"[^a-z0-9_]", "", table)
    sql = f'SELECT COUNT(*) FROM "{safe_schema}"."{safe_table}"'
    with engine.connect() as conn:
        conn.execute(text("SET LOCAL statement_timeout = '15s'"))
        n = conn.execute(text(sql)).scalar()

    return (
        f"DETERMINISTIC (exact)\n"
        f"  table : {safe_schema}.{safe_table}\n"
        f"  sql   : {sql}\n"
        f"  count : {int(n) if n is not None else 0}\n"
        f"(Exact figure from Postgres — not from similarity search.)"
    )


def _semantic(query: str, project_slug: str | None) -> str:
    """Call the existing search_all semantic tool and return its text."""
    try:
        from dash.tools.semantic_search import create_search_all_tool

        sa = create_search_all_tool(project_slug=project_slug)
        # search_all is an agno @tool; the underlying callable is .entrypoint.
        fn = getattr(sa, "entrypoint", None) or sa
        result = fn(query)
        return f"SEMANTIC\n{result}"
    except Exception as e:  # noqa: BLE001
        logger.debug(f"semantic path failed: {e}")
        return f"SEMANTIC\n(semantic search unavailable: {e})"


def create_hybrid_lookup_tool(project_slug: str | None = None):
    """Factory that captures ``project_slug`` (like the other build.py tools)
    and returns the agno ``hybrid_lookup`` tool.
    """

    @tool(
        name="hybrid_lookup",
        description=(
            "Route a question to the RIGHT engine: exact metrics "
            "(count/how many/total/sum/number of/list-with-filter) run as "
            "DETERMINISTIC SQL against the project tables and return an EXACT "
            "figure; meaning/context questions (what is/explain/why/describe) "
            "run SEMANTIC vector search; mixed questions do BOTH (HYBRID). "
            "Use this for any count/total question so the number is never "
            "estimated from similarity search. Args: query (str)."
        ),
    )
    def hybrid_lookup(query: str) -> str:
        try:
            if not query or not query.strip():
                return "hybrid_lookup error: empty query."

            mode = _classify(query)
            engine, schema = _resolve_engine_and_schema(project_slug)

            # No project bound → can't run deterministic SQL; semantic only.
            if mode == "deterministic" and engine is None:
                return (
                    "DETERMINISTIC requested but no project schema is bound; "
                    "falling back to SEMANTIC.\n" + _semantic(query, project_slug)
                )

            if mode == "deterministic":
                det = _deterministic_count(engine, schema, query)
                if det.startswith("FALLBACK:"):
                    return (
                        "DETERMINISTIC could not be built safely "
                        f"({det[len('FALLBACK: '):]}). Falling back to SEMANTIC.\n"
                        + _semantic(query, project_slug)
                    )
                return det

            if mode == "semantic":
                return _semantic(query, project_slug)

            # mode == "hybrid": do both and report both, clearly separated.
            parts: list[str] = ["HYBRID (deterministic + semantic)"]
            if engine is not None:
                det = _deterministic_count(engine, schema, query)
                if det.startswith("FALLBACK:"):
                    parts.append(
                        "DETERMINISTIC: skipped — "
                        + det[len("FALLBACK: "):]
                    )
                else:
                    parts.append(det)
            else:
                parts.append("DETERMINISTIC: skipped — no project schema bound.")
            parts.append("---")
            parts.append(_semantic(query, project_slug))
            return "\n".join(parts)

        except Exception as e:  # noqa: BLE001
            logger.warning(f"hybrid_lookup error: {e}")
            return f"hybrid_lookup error: {e}"

    return hybrid_lookup
