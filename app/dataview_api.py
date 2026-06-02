"""
Dataview API — Obsidian-Dataview-style NL→SQL queries over warehouse METADATA only.

Hard-constrained to a small whitelist of meta tables; rejects any non-SELECT statement
via regex BEFORE execution; caps rows at 1000 and statement_timeout at 10s.

Preset queries are hardcoded (safer than NL).
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any

from fastapi import APIRouter, HTTPException
from sqlalchemy import text

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dataview", tags=["dataview"])

# ---- security ----------------------------------------------------------------

ALLOWED_TABLES = [
    "dash.dash_tables_meta",
    "dash.dash_columns_meta",
    "dash.dash_metrics",
    "dash.dash_queries_log",
    "information_schema.tables",
    "information_schema.columns",
]

# Reject any of these tokens anywhere in the SQL (case-insensitive, word boundary).
_DENY_RE = re.compile(
    r"\b(DROP|INSERT|UPDATE|DELETE|ALTER|CREATE|TRUNCATE|GRANT|REVOKE|COPY|VACUUM|REINDEX|CLUSTER|ATTACH|DETACH|CALL|DO)\b",
    re.IGNORECASE,
)
_MULTI_STMT_RE = re.compile(r";\s*\S")  # ; followed by non-whitespace = second stmt
_SELECT_HEAD_RE = re.compile(r"^\s*(WITH|SELECT)\b", re.IGNORECASE)

ROW_CAP = 1000
STATEMENT_TIMEOUT_MS = 10000


def _validate_sql(sql: str) -> str:
    """Raise HTTPException if SQL is not a safe SELECT/WITH over whitelisted tables.
    Returns the stripped+single-statement SQL on success.
    """
    if not sql or not sql.strip():
        raise HTTPException(400, "empty sql")
    s = sql.strip().rstrip(";").strip()
    if not _SELECT_HEAD_RE.search(s):
        raise HTTPException(400, "only SELECT/WITH queries are allowed")
    if _DENY_RE.search(s):
        raise HTTPException(400, "destructive keyword detected (DROP/INSERT/UPDATE/DELETE/ALTER/etc.)")
    if _MULTI_STMT_RE.search(s):
        raise HTTPException(400, "multiple statements not allowed")
    # cheap whitelist enforcement: every FROM/JOIN target must reference an allowed table
    # (best-effort — we still wrap in a read-only txn + statement_timeout)
    return s


def _extract_referenced_tables(sql: str) -> list[str]:
    """Pull bare `schema.table` or `table` tokens after FROM/JOIN. Best-effort."""
    out: list[str] = []
    for m in re.finditer(r"\b(?:FROM|JOIN)\s+([a-zA-Z_][\w\.]*)", sql, re.IGNORECASE):
        out.append(m.group(1).lower())
    return out


def _all_referenced_whitelisted(sql: str) -> bool:
    refs = _extract_referenced_tables(sql)
    if not refs:
        # no FROM clause is fine (e.g. SELECT 1) but unusual; allow.
        return True
    allow = {t.lower() for t in ALLOWED_TABLES}
    # also allow unqualified information_schema.* if user wrote bare table name
    allow_bare = {t.split(".")[-1].lower() for t in ALLOWED_TABLES}
    for r in refs:
        r_low = r.lower()
        if r_low in allow:
            continue
        if r_low in allow_bare:
            continue
        return False
    return True


# ---- engine helper -----------------------------------------------------------


def _ro_engine():
    """Read-only engine for executing safe SELECTs over public/dash meta."""
    try:
        from db.session import get_sql_engine
    except Exception as e:
        raise HTTPException(503, f"db engine unavailable: {e}")
    return get_sql_engine()


def _run_select(sql: str) -> dict[str, Any]:
    """Execute the validated SELECT with statement_timeout + row cap."""
    eng = _ro_engine()
    t0 = time.time()
    try:
        with eng.connect() as conn:
            conn.execute(text(f"SET LOCAL statement_timeout = {STATEMENT_TIMEOUT_MS}"))
            conn.execute(text("SET LOCAL default_transaction_read_only = on"))
            res = conn.execute(text(sql))
            cols = list(res.keys())
            rows_raw = res.fetchmany(ROW_CAP)
            rows = [dict(zip(cols, r)) for r in rows_raw]
        # JSON-safe coerce
        for row in rows:
            for k, v in list(row.items()):
                if v is None or isinstance(v, (str, int, float, bool)):
                    continue
                row[k] = str(v)
        return {
            "ok": True,
            "columns": cols,
            "rows": rows,
            "row_count": len(rows),
            "truncated": len(rows) >= ROW_CAP,
            "elapsed_ms": int((time.time() - t0) * 1000),
        }
    except HTTPException:
        raise
    except Exception as e:
        msg = str(e)
        if "statement timeout" in msg.lower() or "canceling statement" in msg.lower():
            raise HTTPException(504, f"query exceeded {STATEMENT_TIMEOUT_MS}ms timeout")
        logger.exception(f"dataview exec failed: {e}")
        raise HTTPException(400, f"exec failed: {msg[:300]}")


# ---- preset queries ----------------------------------------------------------

# Each preset is a description + a hardcoded SQL string.
# These are SAFE — vetted by hand, hit only whitelisted meta tables.
PRESETS: dict[str, dict[str, str]] = {
    "stale_tables_30d": {
        "label": "Stale tables (no activity in 30d)",
        "description": "Tables in dash.dash_tables_meta whose updated_at is older than 30 days.",
        "sql": """
            SELECT table_schema, table_name, updated_at,
                   EXTRACT(DAY FROM (now() - updated_at))::int AS days_stale
            FROM dash.dash_tables_meta
            WHERE updated_at < (now() - interval '30 days')
            ORDER BY updated_at ASC
            LIMIT 200
        """.strip(),
    },
    "unused_columns": {
        "label": "Unused columns",
        "description": "Columns in dash.dash_columns_meta with usage_count = 0 or NULL.",
        "sql": """
            SELECT table_schema, table_name, column_name, data_type, usage_count
            FROM dash.dash_columns_meta
            WHERE COALESCE(usage_count, 0) = 0
            ORDER BY table_schema, table_name, column_name
            LIMIT 500
        """.strip(),
    },
    "tables_without_descriptions": {
        "label": "Tables without descriptions",
        "description": "Tables in dash.dash_tables_meta where description is NULL or empty.",
        "sql": """
            SELECT table_schema, table_name, updated_at
            FROM dash.dash_tables_meta
            WHERE description IS NULL OR length(trim(description)) = 0
            ORDER BY table_schema, table_name
            LIMIT 500
        """.strip(),
    },
    "metrics_with_no_usage": {
        "label": "Metrics with no usage",
        "description": "Rows in dash.dash_metrics where last_used_at is NULL or older than 60 days.",
        "sql": """
            SELECT name, owner, last_used_at, created_at
            FROM dash.dash_metrics
            WHERE last_used_at IS NULL
               OR last_used_at < (now() - interval '60 days')
            ORDER BY COALESCE(last_used_at, created_at) ASC NULLS FIRST
            LIMIT 500
        """.strip(),
    },
}


# ---- NL → SQL prompt ---------------------------------------------------------

_NL_PROMPT_HEADER = """You are a read-only SQL generator over a SMALL meta-schema.

HARD CONSTRAINTS:
- Output ONLY a single PostgreSQL SELECT (or WITH ... SELECT) statement.
- NEVER emit DROP, INSERT, UPDATE, DELETE, ALTER, CREATE, TRUNCATE, GRANT, REVOKE, COPY, or any DDL/DML.
- ONLY reference these tables (no others — if a question needs another table, reply with a SELECT that returns zero rows):
    dash.dash_tables_meta(table_schema, table_name, description, row_count, updated_at, owner, tags)
    dash.dash_columns_meta(table_schema, table_name, column_name, data_type, description, usage_count, last_used_at)
    dash.dash_metrics(name, owner, last_used_at, created_at, description, sql_definition)
    dash.dash_queries_log(query_id, user_id, sql, ran_at, duration_ms, table_schema, table_name)  -- may not exist; if missing, return empty result
    information_schema.tables(table_schema, table_name, table_type)
    information_schema.columns(table_schema, table_name, column_name, data_type)
- Append `LIMIT 1000` if not present.
- Output the SQL alone, no markdown fences, no commentary.

Project slug (for filtering when relevant): {slug}

Question:
{q}
"""


def _nl_to_sql(q: str, slug: str) -> str:
    """Call the project LLM helper to map NL → SQL. Strips fences. Validates."""
    try:
        from dash.settings import training_llm_call
    except Exception as e:
        raise HTTPException(503, f"llm unavailable: {e}")
    prompt = _NL_PROMPT_HEADER.format(slug=slug or "(none)", q=q.strip())
    raw = training_llm_call(prompt, "extraction") or ""
    if not raw.strip():
        raise HTTPException(502, "llm returned empty response")
    # strip markdown fences if present
    s = raw.strip()
    if s.startswith("```"):
        # remove opening ``` (optionally with sql tag) and closing ```
        s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    s = s.strip().rstrip(";").strip()
    return s


# ---- endpoints ---------------------------------------------------------------


@router.post("/run")
def run_query(body: dict[str, Any]) -> dict[str, Any]:
    """NL → SQL over the limited meta schema. Body: {q: str, project_slug: str}."""
    q = (body.get("q") or "").strip()
    slug = (body.get("project_slug") or "").strip()
    if not q:
        raise HTTPException(400, "q required")
    sql = _nl_to_sql(q, slug)
    safe_sql = _validate_sql(sql)
    if not _all_referenced_whitelisted(safe_sql):
        raise HTTPException(
            400,
            f"sql references non-whitelisted table(s); allowed: {', '.join(ALLOWED_TABLES)}",
        )
    result = _run_select(safe_sql)
    result["generated_sql"] = safe_sql
    result["question"] = q
    return result


@router.get("/examples")
def list_examples() -> dict[str, Any]:
    """List preset queries (id + label + description)."""
    return {
        "presets": [
            {"id": name, "label": p["label"], "description": p["description"]}
            for name, p in PRESETS.items()
        ]
    }


@router.post("/preset/{name}")
def run_preset(name: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run a hardcoded preset query. Body may include {project_slug}."""
    p = PRESETS.get(name)
    if not p:
        raise HTTPException(404, f"preset '{name}' not found")
    sql = p["sql"]
    safe_sql = _validate_sql(sql)
    if not _all_referenced_whitelisted(safe_sql):
        raise HTTPException(500, "preset references non-whitelisted table (bug)")
    result = _run_select(safe_sql)
    result["generated_sql"] = safe_sql
    result["preset"] = name
    result["label"] = p["label"]
    return result
