"""Live remote-DB query tool for Analyst.

Lets the agent run read-only SELECT queries directly against a connected
remote source (Postgres / MySQL / Fabric) without the synced project copy.

Lookup is by `project_slug` -> first active source flagged `mode=live`
in `dash_data_sources.config`.
"""
from __future__ import annotations

import base64
import json
import logging
from typing import Any

import pandas as pd
from agno.tools import tool
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

logger = logging.getLogger(__name__)

QUERY_TIMEOUT_S = 30
MAX_ROWS = 10_000


def _decode(b64: str) -> str:
    return base64.b64decode(b64.encode("ascii")).decode("utf-8")


def _build_url(cfg: dict) -> str:
    db_type = cfg["db_type"]
    user = cfg["username"]
    pw = cfg["password"]
    host = cfg["host"]
    port = cfg["port"]
    database = cfg["database"]
    if db_type == "postgresql":
        return f"postgresql+psycopg://{user}:{pw}@{host}:{port}/{database}"
    if db_type == "mysql":
        return f"mysql+pymysql://{user}:{pw}@{host}:{port}/{database}"
    if db_type == "fabric":
        # pure-Python python-tds (pytds) via sqlalchemy-pytds; encryption +
        # no host-cert validation set in connect_args (see query_live_source).
        return f"mssql+pytds://{user}:{pw}@{host}:{port}/{database}"
    raise ValueError(f"Unsupported db_type: {db_type}")


def _resolve_live_source(project_slug: str) -> dict | None:
    """Return decoded config of first live source for project, or None."""
    from db.session import get_engine
    eng = get_engine()
    with eng.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, config, source_type FROM public.dash_data_sources "
            "WHERE project_slug = :slug AND status = 'active' "
            "AND source_type IN ('postgresql','mysql','fabric') ORDER BY id"
        ), {"slug": project_slug}).fetchall()
    for row in rows:
        cfg_raw = row[1]
        cfg = cfg_raw if isinstance(cfg_raw, dict) else json.loads(cfg_raw or "{}")
        if cfg.get("mode") in ("live", "hybrid"):
            cfg["source_id"] = row[0]
            if "password_b64" in cfg and "password" not in cfg:
                cfg["password"] = _decode(cfg["password_b64"])
            return cfg
    return None


def get_live_mode(project_slug: str) -> str | None:
    """Return 'live' / 'hybrid' / None for a project."""
    cfg = _resolve_live_source(project_slug)
    return cfg.get("mode") if cfg else None


def create_live_query_tool(project_slug: str):
    """Return an agno @tool bound to a project's live source."""

    @tool(
        name="query_live_source",
        description=(
            "REAL-TIME query against the source database. PREFERRED tool for any "
            "question about CURRENT state (stock NOW, today's data, latest values, "
            "real-time inventory). Returns LIVE data — never stale. "
            "Use plain unqualified table names (e.g. 'stores', not 'public.stores'). "
            "Only SELECT statements. Max 10000 rows. 30s timeout. "
            "Args: sql (str — single SELECT statement)."
        ),
    )
    def query_live_source(sql: str) -> str:
        cfg = _resolve_live_source(project_slug)
        if not cfg:
            return "ERROR: no live source configured for this project. Use SQL on local schema instead."

        sql_upper = sql.strip().upper()
        if any(sql_upper.startswith(kw) for kw in (
            "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "TRUNCATE", "GRANT", "REVOKE"
        )):
            return "ERROR: only SELECT queries allowed."

        eng = None
        try:
            if cfg["db_type"] == "fabric":
                # pytds: login_timeout/timeout + skip host-cert validation
                # (Fabric SQL endpoints present non-matching certs).
                connect_args = {
                    "login_timeout": QUERY_TIMEOUT_S,
                    "timeout": QUERY_TIMEOUT_S,
                    "validate_host": False,
                }
            else:
                connect_args = {"connect_timeout": QUERY_TIMEOUT_S}
            eng = create_engine(
                _build_url(cfg),
                poolclass=NullPool,
                connect_args=connect_args,
            )
            # Cost pre-flight (EXPLAIN-based). Postgres only — other dialects
            # fail-open inside the guard. Returns warning to agent instead of
            # running an over-budget query. Fail-soft on any guard error.
            if cfg["db_type"] == "postgresql":
                try:
                    from dash.tools.sql_cost_guard import guard_or_note
                    warn = guard_or_note(eng, sql)
                    if warn:
                        logger.info(f"Live query blocked by cost guard: {warn}")
                        return (
                            f"QUERY TOO EXPENSIVE: {warn} "
                            "Add WHERE filters or a LIMIT and try again — "
                            "the query was NOT executed."
                        )
                except Exception as ge:
                    logger.debug(f"Live query cost guard skipped: {ge}")

            with eng.connect() as conn:
                if cfg["db_type"] == "postgresql":
                    conn.execute(text("SET TRANSACTION READ ONLY"))
                    conn.execute(text(f"SET statement_timeout = '{QUERY_TIMEOUT_S * 1000}'"))
                elif cfg["db_type"] == "mysql":
                    conn.execute(text(f"SET SESSION MAX_EXECUTION_TIME = {QUERY_TIMEOUT_S * 1000}"))
                df = pd.read_sql(text(sql), conn)
            truncated = len(df) > MAX_ROWS
            if truncated:
                df = df.head(MAX_ROWS)
            preview = df.head(50).to_dict(orient="records")
            note = f" (truncated to {MAX_ROWS})" if truncated else ""
            return json.dumps({
                "source": "live",
                "db_type": cfg["db_type"],
                "rows": len(df),
                "columns": list(df.columns),
                "data": preview,
                "note": f"Showing first 50 of {len(df)} rows{note}",
            }, default=str)
        except Exception as e:
            logger.warning(f"Live query failed: {e}")
            return f"ERROR running live query: {str(e)[:300]}"
        finally:
            if eng:
                eng.dispose()

    return query_live_source
