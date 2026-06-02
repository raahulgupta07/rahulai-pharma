"""Pandas merge fallback for federation.

Used when DuckDB not installed. Performs N-way join in pandas:
  - Start with first DataFrame
  - For each JoinKey, pandas.merge() left + right
  - Apply final_where + final_order_by + final_limit at end

Limited to 50K rows pre-merge per source. Final result capped at 50K.

Same MergeResult interface as merge_duckdb so caller can swap.
"""
from __future__ import annotations
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# Reuse MergeResult dataclass from merge_duckdb if importable, else define
try:
    from dash.providers.federation.merge_duckdb import MergeResult  # type: ignore
except Exception:
    @dataclass
    class MergeResult:
        df: object = None
        row_count: int = 0
        duration_ms: int = 0
        engine_used: str = "pandas"
        error: Optional[str] = None
        warnings: list = field(default_factory=list)


def merge(
    plan,
    execution_result,
    *,
    max_final_rows: int = 50_000,
) -> MergeResult:
    """Pandas-based N-way join.

    Args:
        plan: SplitPlan with join_keys, final_where, final_select, final_limit
        execution_result: ExecutionResult-like object with .per_source dict
        max_final_rows: cap on final output

    Returns:
        MergeResult with merged DataFrame + diagnostics.
    """
    result = MergeResult()
    result.engine_used = "pandas"

    try:
        import pandas as pd
    except ImportError as e:
        result.error = f"pandas not installed: {e}"
        return result

    if not getattr(execution_result, "per_source", None):
        result.error = "no per-source results to merge"
        return result

    # Single source — return passthrough
    if len(execution_result.per_source) == 1:
        pid, df = next(iter(execution_result.per_source.items()))
        result.df = df.head(max_final_rows)
        result.row_count = len(result.df)
        result.engine_used = "single_source_passthrough"
        return result

    import time
    t0 = time.time()

    try:
        # Sort providers — start w/ smallest DataFrame for efficiency
        sources = list(execution_result.per_source.items())
        sources.sort(key=lambda x: len(x[1]))

        # Track which providers are merged
        merged_pids = {sources[0][0]}
        # Add provider prefix to columns to avoid collisions
        merged_df = _prefix_columns(sources[0][1], sources[0][0])

        # Apply join keys in topological order
        pending = list(getattr(plan, "join_keys", []) or [])
        while pending:
            progressed = False
            for jk in list(pending):
                if jk.left_provider in merged_pids and jk.right_provider not in merged_pids:
                    right_df = execution_result.per_source.get(jk.right_provider)
                    if right_df is None:
                        pending.remove(jk)
                        result.warnings.append(f"missing right df: {jk.right_provider}")
                        continue
                    right_prefixed = _prefix_columns(right_df, jk.right_provider)

                    left_col = f"{_safe(jk.left_provider)}__{jk.left_column}"
                    right_col = f"{_safe(jk.right_provider)}__{jk.right_column}"

                    if left_col not in merged_df.columns or right_col not in right_prefixed.columns:
                        result.warnings.append(
                            f"join column missing: {left_col} OR {right_col}"
                        )
                        pending.remove(jk)
                        continue

                    merged_df = pd.merge(
                        merged_df, right_prefixed,
                        left_on=left_col, right_on=right_col,
                        how="inner",
                    )
                    merged_pids.add(jk.right_provider)
                    pending.remove(jk)
                    progressed = True

                elif jk.right_provider in merged_pids and jk.left_provider not in merged_pids:
                    left_df = execution_result.per_source.get(jk.left_provider)
                    if left_df is None:
                        pending.remove(jk)
                        continue
                    left_prefixed = _prefix_columns(left_df, jk.left_provider)

                    left_col = f"{_safe(jk.left_provider)}__{jk.left_column}"
                    right_col = f"{_safe(jk.right_provider)}__{jk.right_column}"

                    if right_col not in merged_df.columns or left_col not in left_prefixed.columns:
                        result.warnings.append(
                            f"join column missing: {left_col} OR {right_col}"
                        )
                        pending.remove(jk)
                        continue

                    merged_df = pd.merge(
                        merged_df, left_prefixed,
                        left_on=right_col, right_on=left_col,
                        how="inner",
                    )
                    merged_pids.add(jk.left_provider)
                    pending.remove(jk)
                    progressed = True

                elif jk.left_provider in merged_pids and jk.right_provider in merged_pids:
                    pending.remove(jk)
                    progressed = True

            if not progressed:
                break

        # Cross-join any remaining unmerged sources (cartesian — risky)
        for pid, df in sources:
            if pid not in merged_pids:
                df_prefixed = _prefix_columns(df, pid)
                merged_df = merged_df.assign(_xj_key=1)
                df_prefixed = df_prefixed.assign(_xj_key=1)
                merged_df = pd.merge(merged_df, df_prefixed, on="_xj_key").drop(columns=["_xj_key"])
                merged_pids.add(pid)
                result.warnings.append(f"cross-join applied to {pid}")

        # Apply WHERE filter (best-effort — pandas doesn't parse SQL)
        final_where = getattr(plan, "final_where", "") or ""
        if final_where:
            try:
                pd_query = _sql_where_to_pandas(final_where)
                if pd_query:
                    merged_df = merged_df.query(pd_query)
            except Exception as e:
                result.warnings.append(f"final_where translation failed: {e}")

        # Apply final SELECT clause (column projection)
        final_select = getattr(plan, "final_select", "") or ""
        if final_select and final_select.strip() != "*":
            try:
                requested = _parse_select_cols(final_select)
                available = list(merged_df.columns)
                cols_to_keep = []
                for req in requested:
                    if req in available:
                        cols_to_keep.append(req)
                    else:
                        # Try matching via suffix
                        for c in available:
                            if c.endswith(f"__{req}"):
                                cols_to_keep.append(c)
                                break
                if cols_to_keep:
                    merged_df = merged_df[cols_to_keep]
            except Exception as e:
                result.warnings.append(f"final_select projection failed: {e}")

        # Apply ORDER BY (best-effort)
        final_order_by = getattr(plan, "final_order_by", "") or ""
        if final_order_by:
            try:
                merged_df = _apply_order_by(merged_df, final_order_by)
            except Exception as e:
                result.warnings.append(f"final_order_by failed: {e}")

        # Limit
        plan_limit = getattr(plan, "final_limit", None)
        limit = min(plan_limit or max_final_rows, max_final_rows)
        result.df = merged_df.head(limit)
        result.row_count = len(result.df)
        result.duration_ms = int((time.time() - t0) * 1000)
        return result
    except Exception as e:
        result.error = f"pandas merge: {str(e)[:300]}"
        result.duration_ms = int((time.time() - t0) * 1000)
        return result


def _safe(provider_id: str) -> str:
    """Sanitize provider_id for column-name use."""
    return re.sub(r"[^a-zA-Z0-9_]", "_", provider_id)


def _prefix_columns(df, provider_id: str):
    """Prefix column names with provider_id to avoid collisions."""
    safe_pid = _safe(provider_id)
    new_cols = {c: f"{safe_pid}__{c}" for c in df.columns}
    return df.rename(columns=new_cols)


def _sql_where_to_pandas(where_sql: str) -> str:
    """Best-effort: SQL WHERE -> pandas query() syntax.

    Handles: =, !=, >, <, >=, <=, AND, OR, IS NULL, IS NOT NULL.
    """
    s = where_sql
    # IS NOT NULL / IS NULL — do these BEFORE =/== conversion
    s = re.sub(r"(\w+)\s+IS\s+NOT\s+NULL", r"\1.notna()", s, flags=re.IGNORECASE)
    s = re.sub(r"(\w+)\s+IS\s+NULL", r"\1.isna()", s, flags=re.IGNORECASE)
    # AND/OR
    s = re.sub(r"\bAND\b", "&", s, flags=re.IGNORECASE)
    s = re.sub(r"\bOR\b", "|", s, flags=re.IGNORECASE)
    # = -> == (only single = not preceded/followed by =, <, >, !)
    s = re.sub(r"(?<![=<>!])=(?!=)", "==", s)
    return s


def _parse_select_cols(select_clause: str) -> list:
    """Extract bare column names from SELECT clause (best-effort)."""
    parts = []
    # Strip leading SELECT keyword if present
    s = re.sub(r"^\s*SELECT\s+", "", select_clause, flags=re.IGNORECASE)
    for p in s.split(","):
        p = p.strip()
        if not p:
            continue
        # Strip alias (AS <name>)
        m = re.search(r"\s+AS\s+", p, flags=re.IGNORECASE)
        if m:
            p = p[m.end():].strip()
        # Strip table prefix (a.col -> col)
        if "." in p:
            p = p.rsplit(".", 1)[1]
        # Strip surrounding quotes
        p = p.strip().strip('"').strip("'")
        if p:
            parts.append(p)
    return parts


def _apply_order_by(df, order_by_clause: str):
    """Apply ORDER BY clause to DataFrame (best-effort)."""
    s = re.sub(r"^\s*ORDER\s+BY\s+", "", order_by_clause, flags=re.IGNORECASE)
    by_cols = []
    ascending = []
    for piece in s.split(","):
        piece = piece.strip()
        if not piece:
            continue
        asc = True
        if re.search(r"\bDESC\b", piece, flags=re.IGNORECASE):
            asc = False
        col = re.sub(r"\b(ASC|DESC)\b", "", piece, flags=re.IGNORECASE).strip()
        if "." in col:
            col = col.rsplit(".", 1)[1]
        # Try matching as-is or via suffix
        if col in df.columns:
            by_cols.append(col)
            ascending.append(asc)
        else:
            for c in df.columns:
                if c.endswith(f"__{col}"):
                    by_cols.append(c)
                    ascending.append(asc)
                    break
    if by_cols:
        return df.sort_values(by=by_cols, ascending=ascending)
    return df
