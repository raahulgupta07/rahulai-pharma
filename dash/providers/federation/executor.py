"""Parallel federated executor.

Given a SplitPlan, runs each subquery against its provider in parallel
via asyncio.gather. Caps per-source rows + per-source timeout.

Returns dict: provider_id -> DataFrame.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

PER_SOURCE_TIMEOUT_S = 30
PER_SOURCE_ROW_CAP = 10_000


@dataclass
class ExecutionResult:
    per_source: dict = field(default_factory=dict)   # pid -> DataFrame
    errors: dict = field(default_factory=dict)       # pid -> error str
    durations_ms: dict = field(default_factory=dict)
    total_rows: int = 0
    truncated: bool = False                          # any source hit cap


async def execute_split_plan(
    plan: Any,
    *,
    project_slug: str,
    per_source_timeout_s: float = PER_SOURCE_TIMEOUT_S,
    per_source_row_cap: int = PER_SOURCE_ROW_CAP,
) -> ExecutionResult:
    """Run each subquery in plan.subqueries against its provider in parallel.

    Strategy:
      - Resolve providers via the registry singleton.
      - For each SourceSubquery, translate canonical SQL -> provider dialect,
        cap row count, then dispatch via ``asyncio.to_thread`` so the
        synchronous SQLAlchemy call does not block the event loop.
      - Each task is wrapped in ``asyncio.wait_for`` for per-source timeout.
      - All tasks are awaited together via ``asyncio.gather``.
    """
    result = ExecutionResult()

    if not plan.subqueries:
        return result

    try:
        from dash.providers import get_registry

        registry = get_registry()
        providers_by_id = {
            p.id: p for p in registry.list_for_project(project_slug)
        }
    except Exception as e:  # pragma: no cover - defensive
        for sq in plan.subqueries:
            result.errors[sq.provider_id] = f"registry: {e}"
        return result

    async def _run_one(sq: Any):
        pid = sq.provider_id
        if pid not in providers_by_id:
            return pid, None, f"unknown provider: {pid}", 0

        provider = providers_by_id[pid]
        if getattr(provider, "degraded", False):
            return (
                pid,
                None,
                f"degraded: {getattr(provider, 'last_error', 'unknown')}",
                0,
            )

        # Translate canonical (postgres) SQL into the provider's dialect.
        try:
            from dash.providers.federation.translator import to_dialect

            sql_translated = to_dialect(
                sq.sql, provider.dialect, source_dialect="postgres"
            )
        except Exception as e:
            sql_translated = sq.sql
            logger.warning("translate failed for %s: %s", pid, e)

        # Cap rows so a runaway source can't blow up the merge step.
        sql_capped = _add_limit(
            sql_translated, provider.dialect, per_source_row_cap
        )

        t0 = time.time()
        try:
            df = await asyncio.wait_for(
                asyncio.to_thread(_execute_sync, provider, sql_capped),
                timeout=per_source_timeout_s,
            )
            dt_ms = int((time.time() - t0) * 1000)
            return pid, df, None, dt_ms
        except asyncio.TimeoutError:
            return (
                pid,
                None,
                f"timeout after {per_source_timeout_s}s",
                int((time.time() - t0) * 1000),
            )
        except Exception as e:
            return pid, None, str(e)[:300], int((time.time() - t0) * 1000)

    tasks = [_run_one(sq) for sq in plan.subqueries]
    results = await asyncio.gather(*tasks, return_exceptions=False)

    for pid, df, err, dt_ms in results:
        result.durations_ms[pid] = dt_ms
        if err:
            result.errors[pid] = err
        if df is not None:
            result.per_source[pid] = df
            try:
                rowcount = len(df)
            except Exception:
                rowcount = 0
            result.total_rows += rowcount
            if rowcount >= per_source_row_cap:
                result.truncated = True

    return result


def _execute_sync(provider: Any, sql: str):
    """Run SQL via the provider's read-only engine. Returns a pandas DataFrame.

    Routes to ``file_executor.execute_file_sql`` when ``provider.dialect``
    is ``"files"`` (PPTX/PDF/DOCX/XLSX-extracted virtual tables).

    Lazy-imports pandas + sqlalchemy so callers without those deps installed
    can still import this module.
    """
    if getattr(provider, "dialect", "") == "files":
        try:
            from dash.providers.federation.file_executor import execute_file_sql
            return execute_file_sql(provider, sql)
        except Exception as e:
            raise RuntimeError(f"file_executor failed: {e}")

    try:
        import pandas as pd  # noqa: F401
        from sqlalchemy import text
    except ImportError as e:  # pragma: no cover
        raise RuntimeError(f"pandas/sqlalchemy not available: {e}")

    eng = provider.engine_ro
    if eng is None:
        raise RuntimeError(
            f"provider {provider.id} has no read-only engine"
        )

    import pandas as pd

    with eng.connect() as conn:
        df = pd.read_sql(text(sql), conn)
    return df


def _add_limit(sql: str, dialect: str, n: int) -> str:
    """Append a row cap if the SQL doesn't already have one.

    - T-SQL / Fabric: prepend ``TOP N`` after the leading ``SELECT``.
    - Everything else: append ``LIMIT N``.
    """
    s_lower = sql.lower()
    if (
        re.search(r"\blimit\s+\d+\b", s_lower)
        or re.search(r"\btop\s+\d+\b", s_lower)
        or "fetch first" in s_lower
    ):
        return sql

    if dialect in ("tsql", "mssql", "fabric"):
        return re.sub(
            r"^(\s*SELECT)\b",
            f"\\1 TOP {n}",
            sql,
            count=1,
            flags=re.IGNORECASE,
        )
    return sql.rstrip().rstrip(";") + f" LIMIT {n}"
